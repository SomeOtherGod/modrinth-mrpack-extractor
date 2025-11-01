#!/usr/bin/env python3
# extract_mrpack.py
# Usage: python extract_mrpack.py path/to/file.mrpack [--outdir path/to/output]
# Author: SOG (SomeOtherGod)

import argparse
import zipfile
import sys
import json
import shutil
import hashlib
from pathlib import Path
import concurrent.futures
import itertools
import os
import time

try:
    import requests
except Exception:
    requests = None

try:
    # import tqdm optionally;
    from tqdm import tqdm as _tqdm
except Exception:
    _tqdm = None

CHUNK_SIZE = 8192


def sanitize_for_filename(name: str) -> str:
    """Make a filesystem-safe folder name from the pack name."""
    # keep alnum and a few punctuation chars, replace others with underscore
    return "".join(c if (c.isalnum() or c in " ._-()") else "_" for c in name).strip()


def find_index_and_overrides(z: zipfile.ZipFile):
    """Return (index_member_name or None, has_overrides_bool, overrides_prefix)
    overrides_prefix is the exact path inside the zip that corresponds to the overrides root (e.g. 'overrides/') or None.
    """
    namelist = z.namelist()
    index_member = None
    overrides_prefix = None

    # find modrinth.index.json
    for name in namelist:
        if name.replace('\\', '/').split('/')[-1].lower() == 'modrinth.index.json':
            index_member = name
            break

    # find overrides root
    for name in namelist:
        n = name.replace('\\', '/')
        if n.startswith('overrides/') or n == 'overrides' or n == 'overrides/':
            overrides_prefix = 'overrides/'
            break

    return index_member, (overrides_prefix is not None), overrides_prefix


def extract_overrides(z: zipfile.ZipFile, overrides_prefix: str, dest: Path):
    """Extract all files under overrides_prefix into dest, preserving subpaths but stripping the overrides/ prefix."""
    members = [n for n in z.namelist() if n.replace('\\', '/').startswith(overrides_prefix)]
    if not members:
        return 0
    count = 0
    for member in members:
        relpath = member.replace('\\', '/')[len(overrides_prefix):]
        if not relpath:
            continue
        target = dest.joinpath(relpath)
        if member.endswith('/'):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(member) as src, open(target, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        count += 1
    return count


def download_file(url: str, dest: Path, expected_hashes: dict | None = None, position: int | None = None, total_size: int | None = None):
    """Download url to dest with per-file tqdm progress bar and optional hash verification.
    Returns True on success; raises on failure.
    """
    if requests is None:
        raise RuntimeError("requests library is required to download files. Install with: pip install -r requirements.txt")
    if _tqdm is None:
        raise RuntimeError("tqdm is required for progress bars. Install with: pip install -r requirements.txt")

    dest.parent.mkdir(parents=True, exist_ok=True)
    h1 = hashlib.sha1() if expected_hashes and 'sha1' in expected_hashes else None
    h512 = hashlib.sha512() if expected_hashes and 'sha512' in expected_hashes else None

    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            # prefer provided total_size, fallback to Content-Length header (use 0 when missing)
            try:
                total = int(total_size) if total_size else int(r.headers.get('Content-Length', 0))
            except Exception:
                total = 0

            # create progress bar
            pbar = _tqdm(total=total, unit='B', unit_scale=True, unit_divisor=1024, desc=dest.name, position=position, leave=False)
            try:
                with open(dest, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        if h1:
                            h1.update(chunk)
                        if h512:
                            h512.update(chunk)
                        pbar.update(len(chunk))
            finally:
                pbar.close()
    except Exception:
        # Clean up partial file
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        raise

    # verify
    if expected_hashes:
        if 'sha1' in expected_hashes and h1:
            got = h1.hexdigest()
            if got.lower() != expected_hashes['sha1'].lower():
                raise ValueError(f"SHA1 mismatch for {dest}: expected {expected_hashes['sha1']}, got {got}")
        if 'sha512' in expected_hashes and h512:
            got = h512.hexdigest()
            if got.lower() != expected_hashes['sha512'].lower():
                raise ValueError(f"SHA512 mismatch for {dest}: expected {expected_hashes['sha512']}, got {got}")
    return True


def process_mrpack(mrpack_path: Path, outdir: Path | None = None, verify_hashes: bool = False, server_files_only: bool = False):
    """Process the mrpack. If outdir is None, derive folder name from modrinth.index.json `name` field.
    If no modrinth.index.json is present, fall back to mrpack filename (without extension).
    """
    if not mrpack_path.exists():
        raise FileNotFoundError(f"mrpack not found: {mrpack_path}")

    try:
        with zipfile.ZipFile(mrpack_path, 'r') as z:
            index_member, has_overrides, overrides_prefix = find_index_and_overrides(z)

            pack_data = None
            if index_member:
                raw = z.read(index_member)
                pack_data = json.loads(raw.decode('utf-8'))

            # determine outdir from pack name if not provided
            if outdir is None:
                if pack_data and isinstance(pack_data, dict) and 'name' in pack_data and pack_data['name']:
                    folder_name = sanitize_for_filename(str(pack_data['name']))
                    outdir = mrpack_path.parent.joinpath(folder_name)
                else:
                    outdir = mrpack_path.with_suffix('')

            outdir.mkdir(parents=True, exist_ok=True)

            # Now extract overrides (if present)
            if has_overrides and overrides_prefix:
                print(f"Extracting overrides into {outdir}")
                cnt = extract_overrides(z, overrides_prefix, outdir)
                print(f"Extracted {cnt} files from overrides.")
            else:
                print("No overrides/ folder found inside the mrpack.")

            if not pack_data:
                print("modrinth.index.json not found inside the mrpack; skipping downloads.")
                return

            files = pack_data.get('files', [])
            if not files:
                print('No files listed in modrinth.index.json')
                return

            # filter files if requested
            files_to_download = []
            for entry in files:
                if server_files_only:
                    env = entry.get('env', {}) or {}
                    server_env = env.get('server')
                    # treat missing server field as 'unknown'
                    if server_env is None:
                        server_env = 'unknown'
                    if server_env not in ('required', 'optional', 'unknown'):
                        continue
                files_to_download.append(entry)

            total = len(files_to_download)
            if total == 0:
                print('No files to download after applying filters.')
                return

            # Prepare parallel downloads
            max_workers = min(8, (os.cpu_count() or 4) * 2)
            position_counter = itertools.count(0)

            futures = {}
            scheduled = 0
            succeeded = 0
            failed = 0
            start_time = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                # start timer when we begin scheduling downloads
                start_time = time.perf_counter()
                for idx, entry in enumerate(files_to_download, start=1):
                    path = entry.get('path')
                    if not path:
                        print(f'[{idx}/{total}] Skipping file entry without path')
                        continue
                    downloads = entry.get('downloads') or []
                    if isinstance(downloads, str):
                        downloads = [downloads]
                    if not downloads:
                        print(f'[{idx}/{total}] No download URL for {path}; skipping')
                        continue
                    url = downloads[0]
                    dest = outdir.joinpath(path)
                    expected_hashes = entry.get('hashes') if verify_hashes else None
                    size = entry.get('fileSize') or None
                    position = next(position_counter)
                    print(f'[{idx}/{total}] Scheduling {url} -> {dest}')
                    fut = ex.submit(download_file, url, dest, expected_hashes, position, size)
                    futures[fut] = (idx, total, path, url, dest)
                    scheduled += 1

                # Wait for downloads to complete and report results
                for fut in concurrent.futures.as_completed(futures):
                    idx, total, path, url, dest = futures[fut]
                    try:
                        fut.result()
                    except Exception as e:
                        failed += 1
                        print(f'[{idx}/{total}] Failed to download {url}: {e}')
                    else:
                        succeeded += 1
            # end with ThreadPoolExecutor
            end_time = time.perf_counter() if start_time is not None else None
            # Print summary of downloads
            if start_time is not None:
                elapsed = end_time - start_time
                print(f"Downloaded {succeeded}/{scheduled} files in {elapsed:.2f}s")
            else:
                print(f"No downloads were scheduled.")
    except zipfile.BadZipFile:
        raise RuntimeError(f"File is not a valid zip archive: {mrpack_path}")


def main():
    parser = argparse.ArgumentParser(description='Reads a .mrpack (Modrinth pack): extracts the overrides into an overrides/ folder and downloads the files (mods/assets) listed in modrinth.index.json so the Modrinth launcher is not required.')
    parser.add_argument('mrpack', type=str, help='Path to the .mrpack (zip) file')
    parser.add_argument('--outdir', '-o', type=str, default=None, help='Output directory (defaults to name from modrinth.index.json or <mrpack-name>/ next to the mrpack file)')
    parser.add_argument('--verify-hashes', action='store_true', help='Verify sha1/sha512 hashes when present in modrinth.index.json')
    parser.add_argument('--server-files-only', action='store_true', help='Only download files whose "env.server" is required, optional or unknown')
    args = parser.parse_args()

    mrpack_path = Path(args.mrpack).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve() if args.outdir else None

    try:
        process_mrpack(mrpack_path, outdir=outdir, verify_hashes=args.verify_hashes, server_files_only=args.server_files_only)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(2)


if __name__ == '__main__':
    main()

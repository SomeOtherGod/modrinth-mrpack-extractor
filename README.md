# extract_mrpack.py

A small tool to extract and prepare Modrinth `.mrpack` archives so the Modrinth launcher is not required — useful to create server files or install a modpack manually.

Purpose / Overview

- Extracts the `overrides/` folder from a `.mrpack` (ZIP) and copies its contents into an output directory.
- Reads the `modrinth.index.json` (or `index.json`) inside the pack and downloads files listed in the index (e.g. mods, libraries).
- Default output directory is derived from the pack `name` field in the index file. You can override it with `--outdir`.
- Option `--server-files-only` filters downloads to files that are relevant for servers (see details below).

Requirements

- Python 3.8+
- Install the dependency:

```bash
pip install -r requirements.txt
```

Quick usage

Windows (cmd.exe) examples:

- Default run (output directory derived from pack name):

```cmd
python extract_mrpack.py C:\path\to\example.mrpack
```

- With a specific output directory:

```cmd
python extract_mrpack.py C:\path\to\example.mrpack -o C:\my\output\folder
```

- Download only server-relevant files (skip client-only mods):

```cmd
python extract_mrpack.py example.mrpack --server-files-only
```

- Enable checksum verification (when `sha1`/`sha512` are present in the index):

```cmd
python extract_mrpack.py example.mrpack --verify-hashes
```

Command line help:

```cmd
python extract_mrpack.py --help
```

Behavior / Details

- The tool recognizes both `modrinth.index.json` and `index.json` file names inside the `.mrpack`.
- If `--outdir` is not provided, the tool uses the pack `name` field from the index to create a safe output folder name (non-filesystem characters replaced).
- The content of `overrides/` is extracted into the output directory with the leading `overrides/` prefix removed (e.g. `overrides/config/example.conf` → `<outdir>/config/example.conf`).
- Files listed under the `files` array in the index are downloaded sequentially. The console shows a counter `[i/total]` before each download line so you can estimate progress and remaining files.
- For each file entry the first URL from `downloads` is used. If multiple mirrors are present, the current implementation uses only the first URL.
- When `--verify-hashes` is set, `sha1` and/or `sha512` values (if present in the index) are verified after download. A mismatch is reported as an error for that file.

Server-file filtering (`--server-files-only`)

- When `--server-files-only` is set, only files with `env.server` equal to `required`, `optional`, or (if the field is missing) treated as `unknown` will be downloaded. Files explicitly marked as `client` or other non-server values are skipped.

Error handling & notes

- Entries missing `downloads` or `path` are skipped and reported in the console output.
- Network errors and download failures are printed and the script continues with remaining files.
- If the archive does not contain an index file, the tool will still extract the `overrides/` folder but will not attempt any downloads.
- The tool does not upload or execute any downloaded code.


Purpose statement

This tool exists to avoid requiring the Modrinth launcher when you want to generate server files or manually install a modpack.

License

MIT

Drop custom-track downloads here: .xdelta / .vcdiff patches, or the .zip / .rar /
.7z archives they come in (loose or in sub-folders). Get them from the track
pages on https://ctrcustomtracks.com/ (or scripts/scrape_tracks.py download).

scripts/build_native.sh extracts them into tracks/. Files listed in tracks.json
(by md5 or published file name) get their page's name, creator and cup split;
any other file becomes a track named after the file.

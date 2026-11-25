# What MPG changed in ODM

This repository is OpenDroneMap's ODM with MPG Ranch's changes for surveys flown with the DJI Mavic 3 Multispectral:
missions of about 2,500 RGB photos, RTK camera positions, surveyed ground control points, one global adjustment, and
the crispest map the photos allow. It is used by `mpg-aerial-pipeline`, which drives it and adds the multispectral map.

The base is ODM 3.6.2 plus 121 upstream commits (`681350f`). Every change is one commit on `main` after that one;
`git log 681350f..main` lists them. Changes that alter results sit behind an option. The ones marked "always on" only
change speed, memory or crash handling, or are the cubic redraws, the bounded brightness matching at triangle edges
and the closing of small openings in the surface.

ODM builds OpenSfM, the texturing program (`texrecon`, from mvs-texturing) and `odm_orthophoto` from their own
repositories. Our changes to those three are patch files in `SuperBuild/patches/`, applied by the build right after
the download (`SuperBuild/patches/README.md`). Each commit below carries the patch files it needs.

Licence: AGPL-3.0, as ODM.

## The changes

### Map quality

| Change | What it does | Option | Code touched |
|---|---|---|---|
| Cubic redraws | Photos are undistorted, and the final map is drawn, with cubic instead of bilinear sampling, to keep fine detail | Always on | OpenSfM `undistort.py`; `odm_orthophoto` `OdmOrthoPhoto.cpp` |
| Bounded brightness matching at triangle edges | `texrecon` matches brightness between photos with a correction at each triangle corner. For the one-pixel fringe just outside a triangle it extended the triangle's trend, which runs away on a triangle seen edge-on (the walls of the 2.5D surface at crown edges), and the local matching step then spread that value about 1 m into the patch. The fringe now keeps a value between the three corners'. The multispectral bands are textured as 16-bit logarithms, where this made spots that reached 65,535 and came out as no data, ringed by values many times too bright or dark: 71 holes in the fall 2025 merge (mosscoder/mpg-aerial-pipeline issue 68). On a treed 130 m square of summer 2025 mission 3 (its mesh, cameras and photo choice; real band images): a spot 96 times too bright in red is gone, about 6% of a band's pixels change at all (median 0.03%), 0.02 to 0.05% by more than 3%. In 8-bit color made from the same band images, 0.4 to 0.5% of a channel's pixels change, 0.03 to 0.04% by more than one level, the largest by 39. Not yet run on a whole mission | Always on | texrecon `texture_patch.cpp` |
| Small openings in the surface closed | ODM's mesh cleanup (`ReconstructMesh`) removes faces where the 2.5D surface touches itself and closes the holes that opens, but its hole closing gives up on some: a few dozen openings of three or four edges stay in a mission's mesh. Nothing is drawn there, so each is a triangle of no data in both maps: 38 of the 110 holes inside the fall 2025 multispectral merge, 35 of them in the visible merge too (mosscoder/mpg-aerial-pipeline issue 68). After the cleanup, every opening of up to 8 edges now gets triangles between its own corners; no vertex is added or moved, and texturing colors the new faces from the photos like any other face. On the mesh of fall 2025 mission 07 made again from its kept point cloud with the mission's own commands: no small opening before the cleanup, 58 after it (plan area up to 0.21 m², two of them where the maps have a missing triangle), and 54 closed by this step with 89 triangles; the 4 left are flat or a triangle standing on its own. A 90 m window of that mesh, textured with made-up cameras and photos and drawn at 3.2 cm: three holes (0.115, 0.059 and 0.010 m²) before, none after, and `texrecon` chose a photo for every new face. Not yet run in a mission | Always on | ODM `mesh.py` |

### Speed

| Change | What it does | Option | Code touched |
|---|---|---|---|
| Fast match saves | Matches saved at gzip level 1, not 9: 23 s to 0.2 s at 200 photos, files 5% bigger | Always on | OpenSfM `dataset.py` |
| Uncompressed working images | Undistorted photos saved without compression: 0.62 s to 0.03 s per photo, 63 MB each instead of 42 | Always on | OpenSfM `io.py` |
| Parallel texture saves | Texture images saved in parallel at PNG level 1: `texrecon` 129 s to 59 s | Always on | texrecon `material_lib.cpp` |
| GPU matcher option | Descriptor matching in PyTorch (MPS, CUDA or CPU). ODM's environment has no PyTorch, so the pairs go to `contrib/torch_match/torch_match.py`, run by the Python named in `MPG_TORCH_PYTHON`. No clear gain over the CPU matcher in our tests; kept to explore further | `--matcher-type torch`, off | ODM `config.py`, `osfm.py`; OpenSfM `matching.py`, new `torch_matcher.py`; `contrib/torch_match/` |

### Memory and disk

| Change | What it does | Option | Code touched |
|---|---|---|---|
| Compact texture patches | Texture patches stay 8-bit until worked on: `texrecon` peak 49 GB to 22 GB at 1,862 photos | Always on | texrecon `texture_patch.*`, `generate_texture_patches.cpp`, `texture_atlas.cpp`, `local_seam_leveling.cpp` |
| One-channel patches for one-band images | Texture patches from 16-bit one-band images (the multispectral bands, which `texrecon` loads as three equal channels) keep one float channel, 4 bytes a pixel against 12, and are widened only while one patch is worked on. The files written are identical byte for byte (8 band runs on two 100 m squares of mission 3, on one thread, where `texrecon` repeats itself exactly; a 200-photo 8-bit RGB run is identical too). Peak 535 MB to 481 and 663 MB to 552 on those squares. At full size, where a band peaked at 31 to 32 GB (2024 mission 6), the estimate is about 21 GB, then set by the atlases; not measured yet | Always on | texrecon `texture_patch.*`, `generate_texture_patches.cpp`, `local_seam_leveling.cpp`, `texture_view.h` |
| Texturing from raw photos | Depth maps get small 660 px copies; texturing reads the raw JPEGs and undistorts them in memory. Full-mission disk about 190 GB to 75 GB (estimate) | `--raw-texturing`, default `auto` (on for one-camera RGB) | ODM `config.py`, `osfm.py`, `run_opensfm.py`, `mvstex.py`; OpenSfM `undistort.py`, `export_visualsfm`; texrecon `texture_view.*`, `generate_texture_views.cpp`, `arguments.*`, `texrecon.cpp` |
| Fork defaults | `--raw-texturing auto`; mesh size scales with photo count (1,000 vertices per photo, at least 200,000) | Default | ODM `config.py`, `utils.py`, `run_opensfm.py`, `mvstex.py`, `odm_meshing.py` |

### Inputs and crash handling

| Change | What it does | Option | Code touched |
|---|---|---|---|
| Bad maker note | A photo with a malformed maker note is read without it, with a warning, instead of crashing the job | Always on | ODM `photo.py` |
| Tilted photos left out | Photos more than 10 degrees off vertical are skipped and listed in `excluded_oblique.txt` (2024 mission 7 had 63) | Default; `--include-oblique` keeps them | ODM `config.py`, `stages/dataset.py` |

### Georeferencing accuracy

| Change | What it does | Option | Code touched |
|---|---|---|---|
| Shift-only GPS offset | With GCPs, the offset between RTK and the GCPs is a shift only (3 numbers), not shift, turn and stretch (7). 2024 mission 7, held-out GCP heights: 12.0 cm RMS against 24.8. Known limit: with fewer than 3 GCPs the starting shift is wrong and the solve can fail | `--gps-bias-shift-only`, off | ODM `config.py`, `osfm.py`; OpenSfM `align.py`, `config.py`, `ba_helpers.cc` |
| Focal length held | The camera solve keeps the focal length it starts with (from `--cameras`) and solves the lens center and distortion. With RTK camera positions and the shift-only GPS offset, the focal length trades against the up-and-down part of the shift (1% looks like 1.2 m at 120 m above ground), so some missions drift: summer 2025 mission 3 moved 0.93% with a 1.4 m false shift. Held, the shift was 10 cm, the five targets fit to 0.6 to 1.7 cm sideways, and the solve's misfit rose 0.07% | `--hold-focal-length`, off | ODM `config.py`, `osfm.py` (sets OpenSfM's `exif_focal_sd` to 0.000001) |
| Camera angles from the gimbal | Each photo's camera angles (OpenSfM's omega, phi, kappa) come from DJI's gimbal yaw, pitch and roll, converted exactly to OpenSfM's convention. ODM's own conversion takes the aircraft's yaw and builds its frame from ECEF axes: on the Mavic 3 Multispectral its angles are 15 to 24 degrees off in heading for every photo and upside down for the photos where DJI writes a gimbal roll of 180 (1,093 of 2,164 in spring 2025 mission 15); DJI's own angles agree with the solve within 0.2 to 3.9 degrees. The angles orient the start of an incremental solve on one flight line: started on the Mac from spring 2025 mission 15's cloud start pair, 5 of the first 9 photos were added 15 to 112 m from RTK with ODM's angles, and none more than 5 m with these. They also start the triangulation solve, which places no photo wrongly with them and 1,135 of 2,164 without | `--gimbal-angles`, off | ODM `config.py`, `photo.py`, `stages/dataset.py` |
| Exact map conversion | Converts the solve to UTM exactly, instead of ODM's straight-line step, which leaves heights low by the Earth's curvature and pulls cameras inward. Map targets: 2024 mission 7 2.7 cm RMS against 22.9; 2025 mission 3 1.8 against 8.8 | Default; `--stock-georef` uses ODM's step | ODM `config.py`, `run_opensfm.py`; OpenSfM `geo.py`, `export_geocoords` |

### Repeatability

| Change | What it does | Option | Code touched |
|---|---|---|---|
| Repeatable pair choice | The photo pairs compared beside each photo's neighbors come from 50 jittered triangulations of the photo positions; the jitter now has a fixed seed, so the same photos give the same pairs and the same solve path on every run. Before, the Mac and the cloud compared 23,950 and 23,965 pairs of the same 2,164 photos | Always on | OpenSfM `pairs_selection.py` |

## Size of the changes

About 859 lines added and 98 removed in 39 files, which is about 0.8% of the roughly 111,000 lines in the parts
touched. Untouched: OpenMVS (depth maps and fusion), the meshing programs, PDAL, GDAL, DEMs, and OpenSfM's feature
finding and core bundle adjustment.

## Building

As upstream ODM: `pixi install --locked`, `pixi run build`, `pixi run test`, on macOS (Apple silicon) and Linux, and
the `Dockerfile` for a CPU image. Nothing extra is needed for our changes; the build applies the helper patches.
Built and tested with pixi 0.84.1, which the `Dockerfile` pins.

On macOS 27 the SDK lists an architecture conda-forge's linker cannot read. Build with the 26.5 SDK:
`export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk CONDA_BUILD_SYSROOT=$SDKROOT`.

## Things to know

- **Later changes build on earlier ones.** The helper patches are numbered in the order they apply.
- **A changed helper patch needs a fresh download of that helper.** The series is applied once per download; delete
  `SuperBuild/src/<name>` (OpenSfM: `SuperBuild/install/bin/opensfm`) and build again.
- **A second ODM call on a finished project needs `--rerun-from`.** ODM compares its options with the ones the last
  call saved and adds keys to its own copy of `--cameras`, so a plain second call sees the lens as changed and starts
  over from the first stage. `mpg-aerial-pipeline` passes the flag.
- **Taking upstream's updates.** `git fetch upstream && git merge upstream/master` on `main`. If upstream moved a
  helper's pinned commit, check that its series still applies (the build stops if a patch does not).

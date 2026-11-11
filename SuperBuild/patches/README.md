# MPG's changes to the programs ODM builds from other repositories

ODM's build downloads OpenSfM, mvs-texturing (`texrecon`) and odm_orthophoto from their own repositories at pinned
commits. Our changes to them are kept here as patch files and applied by the build itself, right after the download
(`SuperBuild/cmake/apply-patch-series.cmake`). Changes to ODM's own code are ordinary commits in this repository.

| Folder | Program | Applied in |
|---|---|---|
| `opensfm/` | OpenSfM, the camera solver | `SuperBuild/cmake/External-OpenSfM.cmake` |
| `mvstexturing/` | mvs-texturing, the texturing program `texrecon` (after ODM's own `cmake/mvstexturing.patch`) | `SuperBuild/cmake/External-MvsTexturing.cmake` |
| `odm_orthophoto/` | the program that draws the orthophoto | `SuperBuild/CMakeLists.txt` |

Patches are applied in name order, so the number at the front of a file name is its place in the series. The same
patches are applied on every system.

To change a helper: edit its source under `SuperBuild/src/<name>` (OpenSfM: `SuperBuild/install/bin/opensfm`), write
the change with `git diff` into a new numbered patch here, and commit it. The series is applied once per download, so
delete the helper's source folder to have a changed series applied on the next build.

What each change does and why: `MPG_CHANGES.md` at the top of the repository.

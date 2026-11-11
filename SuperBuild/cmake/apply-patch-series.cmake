# Applies MPG's changes to one of the programs ODM builds from another repository (SuperBuild/patches/README.md).
#
# Every *.patch of a folder is applied in name order with `git apply`.
#
# Usage: cmake -P apply-patch-series.cmake <folder>
# (run with the external project's source dir as the working directory, as a PATCH_COMMAND is)
#
# The series is applied once. If its last patch is already in the tree (reversing it would apply cleanly), nothing
# is done, so a rerun of the patch step is harmless. After adding or changing a patch, delete the project's source
# folder (SuperBuild/src/<name>, or SuperBuild/install/bin/opensfm) so it is fetched and patched afresh.

set(_dir "${CMAKE_ARGV3}")
file(GLOB _patches "${_dir}/*.patch")
list(SORT _patches)
if(NOT _patches)
  return()
endif()

list(GET _patches -1 _last)
execute_process(
  COMMAND git apply --whitespace=nowarn --reverse --check "${_last}"
  RESULT_VARIABLE _already_applied
  OUTPUT_QUIET
  ERROR_QUIET
)
if(_already_applied EQUAL 0)
  message(STATUS "MPG patches already applied: ${_dir}")
  return()
endif()

foreach(_patch ${_patches})
  execute_process(
    COMMAND git apply --whitespace=nowarn "${_patch}"
    RESULT_VARIABLE _apply_result
  )
  if(NOT _apply_result EQUAL 0)
    message(FATAL_ERROR "git apply failed for: ${_patch}")
  endif()
  message(STATUS "MPG patch applied: ${_patch}")
endforeach()

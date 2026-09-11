#!/bin/bash
# Manual build helper. The workflow uses tcflow.build for signed, atomic builds.
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${BUILD_DIR:?Set BUILD_DIR to a separate build directory}"
FC="${FC:-gfortran}"
NF_CONFIG="${NF_CONFIG:-nf-config}"
mkdir -p "$BUILD_DIR"
BUILD_DIR="$(cd "$BUILD_DIR" && pwd)"
[ "$BUILD_DIR" != "$SOURCE_DIR" ] || { echo 'BUILD_DIR must differ from source directory' >&2; exit 1; }
cp "$SOURCE_DIR"/*.f90 "$BUILD_DIR/"
cd "$BUILD_DIR"
if [ -z "${FFLAGS+x}" ]; then
  case "$(basename "$FC")" in
    gfortran) FFLAGS='-O2 -ffree-line-length-none -mcmodel=large' ;;
    ifort|ifx) FFLAGS='-O2 -free -mcmodel=large -heap-arrays 10' ;;
    *) echo 'Set FFLAGS for this compiler' >&2; exit 1 ;;
  esac
fi
read -r -a FLAGS <<< "$FFLAGS"
read -r -a INCLUDES <<< "$("$NF_CONFIG" --fflags)"
read -r -a LIBRARIES <<< "$("$NF_CONFIG" --flibs)"
"$FC" "${FLAGS[@]}" "${INCLUDES[@]}" -o irt_objects_release.x irt_parameters.f90 irt_tools.f90 irt_objects_release.f90 "${LIBRARIES[@]}"
"$FC" "${FLAGS[@]}" "${INCLUDES[@]}" -o irt_advection_field_release.x irt_parameters.f90 irt_advection_field_release.f90 "${LIBRARIES[@]}"
"$FC" "${FLAGS[@]}" "${INCLUDES[@]}" -o irt_tracks_release.x irt_parameters.f90 irt_tracks_release.f90 "${LIBRARIES[@]}"
"$FC" "${FLAGS[@]}" "${INCLUDES[@]}" -o irt_trackmask_release.x irt_parameters.f90 irt_trackmask_release.f90 "${LIBRARIES[@]}"
"$FC" "${FLAGS[@]}" "${INCLUDES[@]}" -o irt_tracklinks_release.x irt_parameters.f90 irt_tracklinks_release_shao.f90 "${LIBRARIES[@]}"
echo "Built in $BUILD_DIR"

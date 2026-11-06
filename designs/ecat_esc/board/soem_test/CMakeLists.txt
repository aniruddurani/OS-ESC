# The SOEM low-level test against the ESC on the DE2-115 (Windows, MinGW).
#   cmake -G "MinGW Makefiles" -S designs/ecat_esc/board/soem_test -B build/ecat_esc/board/soem
#   mingw32-make -C build/ecat_esc/board/soem
# SOEM is fetched at a pinned tag into the build directory (v2.0.1: the first release whose
# Windows layer builds with GCC; GPLv3 or commercial, used here as a local test tool and not
# distributed). Npcap's SDK (import libraries) is at NPCAP_SDK.
cmake_minimum_required(VERSION 3.28)
project(soem_test C)

set(NPCAP_SDK "C:/npcap-sdk" CACHE PATH "Npcap SDK (Include/, Lib/x64/)")

include(FetchContent)
FetchContent_Declare(soem
    GIT_REPOSITORY https://github.com/OpenEtherCATsociety/SOEM.git
    GIT_TAG        v2.0.1)
set(SOEM_BUILD_SAMPLES OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(soem)

foreach(program soem_test soem_config)
    add_executable(${program} ${program}.c)
    target_link_directories(${program} PRIVATE ${NPCAP_SDK}/Lib/x64)
    target_link_libraries(${program} PRIVATE soem wpcap Packet ws2_32 winmm)
endforeach()

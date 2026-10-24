cmake_minimum_required(VERSION 3.16)
if(DEFINED C5VRX5_VERSION)
    set(PROJECT_VER "${C5VRX5_VERSION}")
else()
    set(PROJECT_VER "5.0.0-dev")
endif()
# Keep explicit defaults (for example the digital sample-clock CI variant).
# An unconditional normal variable would mask a -D SDKCONFIG_DEFAULTS cache entry.
if(NOT DEFINED SDKCONFIG_DEFAULTS)
    set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(c5vrx5)

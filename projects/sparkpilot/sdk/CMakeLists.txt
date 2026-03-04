# CUI // SP-CTI
# SparkPilot Device SDK — CMake build configuration
cmake_minimum_required(VERSION 3.16)

project(sparkpilot_sdk C)

add_library(sparkpilot_sdk STATIC
    src/sparkpilot_sdk.c
)

target_include_directories(sparkpilot_sdk PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(sparkpilot_sdk PUBLIC
    freertos_kernel
)

# Flash size report
if(CMAKE_C_COMPILER_ID STREQUAL "GNU")
    add_custom_command(TARGET sparkpilot_sdk POST_BUILD
        COMMAND ${CMAKE_SIZE} $<TARGET_FILE:sparkpilot_sdk>
        COMMENT "SparkPilot SDK size:"
    )
endif()

set(C5VRX4_FIRMWARE_DIR "${PROJECT_DIR}/firmware")
set(C5VRX4_INCLUDE_DIR "${C5VRX4_FIRMWARE_DIR}/include")
set(C5VRX4_PROGRAM_DIR "${C5VRX4_FIRMWARE_DIR}/programs")

target_sources(${COMPONENT_LIB} PRIVATE "${C5VRX4_FIRMWARE_DIR}/pipeline.c")
target_include_directories(${COMPONENT_LIB} PRIVATE "${C5VRX4_FIRMWARE_DIR}" "${C5VRX4_INCLUDE_DIR}")
target_compile_definitions(${COMPONENT_LIB} PRIVATE C5VRX4_EXPERIMENT=1)

target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_reference_phase8_hr.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_reference_golden.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_static.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_history.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_static_legacy.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_history_legacy.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_static_cvbs150.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_history_cvbs150.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_static_mask.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_static_mask_legacy.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_phase8_static_mask_cvbs150.bsasm")
target_sources(${COMPONENT_LIB} PRIVATE "${C5VRX4_FIRMWARE_DIR}/cvbs_monitor.c")
target_sources(${COMPONENT_LIB} PRIVATE "${C5VRX4_FIRMWARE_DIR}/sync_flywheel.c")

target_sources(${COMPONENT_LIB} PRIVATE "${C5VRX4_FIRMWARE_DIR}/lanes.c")

target_sources(${COMPONENT_LIB} PRIVATE "${C5VRX4_FIRMWARE_DIR}/cvbs_level.c" "${C5VRX4_FIRMWARE_DIR}/cvbs_level_hw.c")

# Explicit native span50 programs, never overwritten by the historical generator.
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_vlp56.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_hc50.bsasm")

target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_ovp56.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_pll96.bsasm")
target_bitscrambler_add_src("${C5VRX4_PROGRAM_DIR}/c5vrx4_range32.bsasm")
foreach(option RANGE 0 3)
    set(program "${C5VRX4_PROGRAM_DIR}/c5vrx4_range_option${option}.bsasm")
    if(EXISTS "${program}")
        target_bitscrambler_add_src("${program}")
    endif()
endforeach()

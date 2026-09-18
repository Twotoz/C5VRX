idf_component_register(
    SRCS
        "main.c"
        "rf.c"
        "video.c"
        "menu_raster.c"
        "link.c"
        "grab.c"
        "uart_link.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_wifi
        nvs_flash
        esp_event
        esp_netif
        esp_driver_parlio
        esp_driver_gpio
        esp_driver_uart
        esp_driver_bitscrambler
        esp_driver_usb_serial_jtag
        vfs
        esp_rom
        esp_mm
        esp_timer
        freertos
        heap
        hal
        esp_hw_support
)

# Proven default plus opt-in experimental 4-bit@80 packer, and the loopback
# twin of the Phase5 demodulator (trailing bytes) for CONFIG_C5VRX_LINK_MODE.
target_bitscrambler_add_src("fm.bsasm")
target_bitscrambler_add_src("fm4.bsasm")
target_bitscrambler_add_src("fm_loop.bsasm")

# The frame grabber runs per-sample loops against the 40 MS/s ring; the
# project default (-Og) is several times too slow for them.
set_source_files_properties(grab.c PROPERTIES COMPILE_OPTIONS "-O2")

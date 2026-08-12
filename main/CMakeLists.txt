idf_component_register(
    SRCS
        "c5vrx_main.c"
        "c5vrx_rf.c"
        "c5vrx_channels.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_phy esp_wifi
)

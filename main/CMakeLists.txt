idf_component_register(
    SRCS
        "c5vrx_main.c"
        "c5vrx_rf.c"
        "c5vrx_wifi5.c"
        "c5vrx_phy_hacks.c"
        "c5vrx_adc_dump.c"
        "c5vrx_channels.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_phy esp_wifi esp_event esp_netif
)

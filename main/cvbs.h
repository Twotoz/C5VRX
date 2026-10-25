/* C5VRX by Twotoz/contributors: passive bounded output-level observer. */
#pragma once
#include "afc.h"
#include "cvbs_level.h"
/* Called by control on the same immutable Q4 window as AFC. An invalid or
 * weak window discards pending evidence, never changes an applied fit. */
void cvbs_observe(const uint8_t *raw,size_t n,const afc2_result_t *r,bool usable,uint32_t context);
void cvbs_print(void);
bool transport_set_cvbs_fit(const cvbs_fit_t *fit);

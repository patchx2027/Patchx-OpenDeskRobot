/* SPDX-License-Identifier: MIT */
#include <stdint.h>
#include <stdlib.h>
#include <speex/speex_echo.h>
#include <speex/speex_preprocess.h>

enum { frame_samples = 256, filter_samples = 4096 };

struct wake_aec {
    SpeexEchoState *echo;
    SpeexPreprocessState *preprocess;
};

void rtctrl_aec_destroy(struct wake_aec *aec)
{
    if (aec == NULL)
        return;
    if (aec->preprocess != NULL)
        speex_preprocess_state_destroy(aec->preprocess);
    if (aec->echo != NULL)
        speex_echo_state_destroy(aec->echo);
    free(aec);
}

struct wake_aec *rtctrl_aec_create(int enable_aes)
{
    int rate = 16000;
    struct wake_aec *aec = calloc(1, sizeof(*aec));
    if (aec == NULL)
        return NULL;

    aec->echo = speex_echo_state_init(frame_samples, filter_samples);
    if (aec->echo == NULL)
        goto fail;
    speex_echo_ctl(aec->echo, SPEEX_ECHO_SET_SAMPLING_RATE, &rate);
    if (enable_aes) {
        aec->preprocess = speex_preprocess_state_init(frame_samples, rate);
        if (aec->preprocess == NULL)
            goto fail;
        speex_preprocess_ctl(aec->preprocess, SPEEX_PREPROCESS_SET_ECHO_STATE, aec->echo);
    }
    return aec;

fail:
    rtctrl_aec_destroy(aec);
    return NULL;
}

int rtctrl_aec_process256(struct wake_aec *aec, const int16_t *mic,
                          const int16_t *speaker, int16_t *output)
{
    if (aec == NULL || mic == NULL || speaker == NULL || output == NULL)
        return -1;
    speex_echo_cancellation(aec->echo, mic, speaker, output);
    if (aec->preprocess != NULL)
        speex_preprocess_run(aec->preprocess, output);
    return 0;
}

void rtctrl_aec_reset(struct wake_aec *aec)
{
    if (aec != NULL)
        speex_echo_state_reset(aec->echo);
}

const char *rtctrl_aec_error(void)
{
    return "SpeexDSP echo cancellation failed";
}

// SPDX-License-Identifier: GPL-2.0-or-later
/*
 *  Copyright (C) 2012, Analog Devices Inc.
 *	Author: Lars-Peter Clausen <lars@metafoo.de>
 *
 *  Based on:
 *	imx-pcm-dma-mx2.c, Copyright 2009 Sascha Hauer <s.hauer@pengutronix.de>
 *	mxs-pcm.c, Copyright (C) 2011 Freescale Semiconductor, Inc.
 *	ep93xx-pcm.c, Copyright (C) 2006 Lennert Buytenhek <buytenh@wantstofly.org>
 *		      Copyright (C) 2006 Applied Data Systems
 */
#include <linux/module.h>
#include <linux/init.h>
#include <linux/dmaengine.h>
#include <linux/slab.h>
#include <linux/mutex.h>
#include <linux/wait.h>
#include <linux/kernel.h>
#include <sound/pcm.h>
#include <sound/pcm_params.h>
#include <sound/soc.h>

#include <sound/dmaengine_pcm.h>
#include "pcm_local.h"

struct dmaengine_pcm_runtime_data {
	struct dma_chan *dma_chan;
	dma_cookie_t cookie;

	unsigned int pos;
	bool checked, exposed, quiescing, closing, go_failed;
	int first_error;
	void (*error_sink)(struct snd_pcm_substream *, int, void *);
	void *error_sink_arg;
	spinlock_t lifecycle_lock;
	atomic_t operations;
	wait_queue_head_t drain_wait;
	struct mutex quiesce_mutex;
	struct snd_pcm_dma_quarantine *quarantine;
	struct snd_dma_buffer *owner_buffer;
	struct snd_dma_buffer owner;
};

static int dmaengine_pcm_check_fault(struct snd_pcm_substream *substream);
static void dmaengine_pcm_notify_fault(struct snd_pcm_substream *substream,
				      int error, bool stop_xrun);

static inline struct dmaengine_pcm_runtime_data *substream_to_prtd(
	const struct snd_pcm_substream *substream)
{
	return substream->runtime->private_data;
}

struct dma_chan *snd_dmaengine_pcm_get_chan(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);

	return prtd->dma_chan;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_get_chan);

/**
 * snd_hwparams_to_dma_slave_config - Convert hw_params to dma_slave_config
 * @substream: PCM substream
 * @params: hw_params
 * @slave_config: DMA slave config
 *
 * This function can be used to initialize a dma_slave_config from a substream
 * and hw_params in a dmaengine based PCM driver implementation.
 */
int snd_hwparams_to_dma_slave_config(const struct snd_pcm_substream *substream,
	const struct snd_pcm_hw_params *params,
	struct dma_slave_config *slave_config)
{
	enum dma_slave_buswidth buswidth;
	int bits;

	bits = params_physical_width(params);
	if (bits < 8 || bits > 64)
		return -EINVAL;
	else if (bits == 8)
		buswidth = DMA_SLAVE_BUSWIDTH_1_BYTE;
	else if (bits == 16)
		buswidth = DMA_SLAVE_BUSWIDTH_2_BYTES;
	else if (bits == 24)
		buswidth = DMA_SLAVE_BUSWIDTH_3_BYTES;
	else if (bits <= 32)
		buswidth = DMA_SLAVE_BUSWIDTH_4_BYTES;
	else
		buswidth = DMA_SLAVE_BUSWIDTH_8_BYTES;

	if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK) {
		slave_config->direction = DMA_MEM_TO_DEV;
		slave_config->dst_addr_width = buswidth;
	} else {
		slave_config->direction = DMA_DEV_TO_MEM;
		slave_config->src_addr_width = buswidth;
	}

	slave_config->device_fc = false;

	return 0;
}
EXPORT_SYMBOL_GPL(snd_hwparams_to_dma_slave_config);

/**
 * snd_dmaengine_pcm_set_config_from_dai_data() - Initializes a dma slave config
 *  using DAI DMA data.
 * @substream: PCM substream
 * @dma_data: DAI DMA data
 * @slave_config: DMA slave configuration
 *
 * Initializes the {dst,src}_addr, {dst,src}_maxburst, {dst,src}_addr_width and
 * slave_id fields of the DMA slave config from the same fields of the DAI DMA
 * data struct. The src and dst fields will be initialized depending on the
 * direction of the substream. If the substream is a playback stream the dst
 * fields will be initialized, if it is a capture stream the src fields will be
 * initialized. The {dst,src}_addr_width field will only be initialized if the
 * SND_DMAENGINE_PCM_DAI_FLAG_PACK flag is set or if the addr_width field of
 * the DAI DMA data struct is not equal to DMA_SLAVE_BUSWIDTH_UNDEFINED. If
 * both conditions are met the latter takes priority.
 */
void snd_dmaengine_pcm_set_config_from_dai_data(
	const struct snd_pcm_substream *substream,
	const struct snd_dmaengine_dai_dma_data *dma_data,
	struct dma_slave_config *slave_config)
{
	if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK) {
		slave_config->dst_addr = dma_data->addr;
		slave_config->dst_maxburst = dma_data->maxburst;
		if (dma_data->flags & SND_DMAENGINE_PCM_DAI_FLAG_PACK)
			slave_config->dst_addr_width =
				DMA_SLAVE_BUSWIDTH_UNDEFINED;
		if (dma_data->addr_width != DMA_SLAVE_BUSWIDTH_UNDEFINED)
			slave_config->dst_addr_width = dma_data->addr_width;
	} else {
		slave_config->src_addr = dma_data->addr;
		slave_config->src_maxburst = dma_data->maxburst;
		if (dma_data->flags & SND_DMAENGINE_PCM_DAI_FLAG_PACK)
			slave_config->src_addr_width =
				DMA_SLAVE_BUSWIDTH_UNDEFINED;
		if (dma_data->addr_width != DMA_SLAVE_BUSWIDTH_UNDEFINED)
			slave_config->src_addr_width = dma_data->addr_width;
	}

	slave_config->slave_id = dma_data->slave_id;
	slave_config->peripheral_config = dma_data->peripheral_config;
	slave_config->peripheral_size = dma_data->peripheral_size;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_set_config_from_dai_data);

static __noreturn void dmaengine_pcm_failstop(const char *reason)
{
	/* Allocation/software ownership cannot be handed back on this path. */
	panic_timeout = 0;
	panic("DMA PCM lifecycle fail-stop: %s", reason);
}

static int dmaengine_pcm_error(struct dmaengine_pcm_runtime_data *prtd, int ret)
{
	unsigned long flags;

	if (!prtd->checked)
		return ret;
	spin_lock_irqsave(&prtd->lifecycle_lock, flags);
	if (ret < 0 && !prtd->first_error)
		prtd->first_error = ret;
	ret = prtd->first_error;
	spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	return ret;
}

static int dmaengine_pcm_operation_begin(struct dmaengine_pcm_runtime_data *prtd,
					int cmd, bool stopping)
{
	unsigned long flags;
	int ret = 0;

	if (!prtd->checked)
		return 0;
	spin_lock_irqsave(&prtd->lifecycle_lock, flags);
	if (prtd->closing || prtd->quiescing)
		ret = prtd->first_error ? prtd->first_error : -EIO;
	else if (!stopping && (prtd->first_error || prtd->go_failed))
		ret = prtd->first_error ? prtd->first_error : -EIO;
	else if (cmd == SNDRV_PCM_TRIGGER_START && prtd->exposed)
		ret = -EBUSY;
	if (!ret)
		atomic_inc(&prtd->operations);
	spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	return ret;
}

static void dmaengine_pcm_operation_end(struct dmaengine_pcm_runtime_data *prtd)
{
	if (prtd->checked) {
		atomic_dec(&prtd->operations);
		wake_up_all(&prtd->drain_wait);
	}
}

int snd_dmaengine_pcm_quiesce(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	struct snd_pcm_runtime *runtime = substream->runtime;
	struct snd_dma_buffer *buffer;
	unsigned long flags;
	int ret, first;

	might_sleep();
	if (!prtd)
		return 0;
	if (!prtd->checked)
		return dmaengine_synchronize_checked(prtd->dma_chan);
	/* PREPARE/HW_FREE may have no descriptor; close CPU admission first. */
	dmaengine_pcm_check_fault(substream);
	mutex_lock(&prtd->quiesce_mutex);
	spin_lock_irqsave(&prtd->lifecycle_lock, flags);
	prtd->quiescing = true;
	spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	/* Atomic trigger callbacks never wait or acquire this process mutex. */
	if (!wait_event_timeout(prtd->drain_wait, !atomic_read(&prtd->operations),
				msecs_to_jiffies(500)))
		dmaengine_pcm_failstop("trigger admission drain deadline");
	/* A checked negative return means all old software callbacks are drained. */
	ret = dmaengine_synchronize_checked(prtd->dma_chan);
	first = dmaengine_pcm_error(prtd, ret);
	if (ret < 0)
		dmaengine_pcm_notify_fault(substream, ret, false);
	if (prtd->exposed) {
		buffer = runtime->dma_buffer_p;
		if (buffer != prtd->owner_buffer || !buffer ||
		    buffer->area != prtd->owner.area || buffer->addr != prtd->owner.addr ||
		    buffer->bytes != prtd->owner.bytes || buffer->dev.type != prtd->owner.dev.type ||
		    buffer->dev.dev != prtd->owner.dev.dev ||
		    buffer->private_data != prtd->owner.private_data ||
		    snd_pcm_dma_quarantine_check(substream, prtd->dma_chan->device->dev))
			dmaengine_pcm_failstop("exposed allocation owner changed");
		if (ret < 0 && snd_pcm_dma_quarantine(substream, prtd->quarantine,
						     prtd->dma_chan->device->dev))
			dmaengine_pcm_failstop("allocation ownership transfer failed");
		prtd->exposed = false;
		prtd->owner_buffer = NULL;
		memset(&prtd->owner, 0, sizeof(prtd->owner));
	}
	/* Void GO reported only DMA_ERROR; keep a later exact checked errno first. */
	if (!first && prtd->go_failed)
		first = dmaengine_pcm_error(prtd, -EIO);
	prtd->cookie = 0;
	spin_lock_irqsave(&prtd->lifecycle_lock, flags);
	if (!prtd->closing)
		prtd->quiescing = false;
	spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	mutex_unlock(&prtd->quiesce_mutex);
	return first;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_quiesce);

static void dmaengine_pcm_dma_complete(void *arg)
{
	unsigned int new_pos;
	struct snd_pcm_substream *substream = arg;
	struct dmaengine_pcm_runtime_data *prtd;

	snd_pcm_stream_lock_irq(substream);
	if (PCM_RUNTIME_CHECK(substream)) {
		snd_pcm_stream_unlock_irq(substream);
		return;
	}

	prtd = substream_to_prtd(substream);

	new_pos = prtd->pos + snd_pcm_lib_period_bytes(substream);
	if (new_pos >= snd_pcm_lib_buffer_bytes(substream))
		new_pos = 0;
	prtd->pos = new_pos;
	snd_pcm_stream_unlock_irq(substream);

	snd_pcm_period_elapsed(substream);
}

/* Called from atomic contexts. The binding is immutable from open to drain. */
static void dmaengine_pcm_notify_fault(struct snd_pcm_substream *substream,
				      int error, bool stop_xrun)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	void (*sink)(struct snd_pcm_substream *, int, void *);
	void *sink_arg;
	unsigned long flags;

	if (!prtd || !prtd->checked)
		return;
	error = dmaengine_pcm_error(prtd, error < 0 ? error : -EIO);
	spin_lock_irqsave(&prtd->lifecycle_lock, flags);
	sink = prtd->error_sink;
	sink_arg = prtd->error_sink_arg;
	spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	/* CPU admission/ticket failure precedes the ALSA state action. */
	if (sink)
		sink(substream, error, sink_arg);
	/* Synchronous trigger/pointer callers may already own the stream lock. */
	if (stop_xrun)
		snd_pcm_stop_xrun(substream);
}

static int dmaengine_pcm_check_fault(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	int error;

	if (!prtd || !prtd->checked)
		return 0;
	/* This accessor is cached, IRQ-safe and does not acquire PM or perform I/O. */
	error = dmaengine_check_open(prtd->dma_chan);
	if (error < 0) {
		dmaengine_pcm_notify_fault(substream, error, false);
		return dmaengine_pcm_error(prtd, error);
	}
	return dmaengine_pcm_error(prtd, 0);
}

static void dmaengine_pcm_dma_complete_result(void *arg,
					    const struct dmaengine_result *result)
{
	struct snd_pcm_substream *substream = arg;
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	int error;

	if (!prtd || !prtd->checked)
		return;
	error = dmaengine_check_open(prtd->dma_chan);
	if (error < 0 || (result && result->result != DMA_TRANS_NOERROR)) {
		/* Preserve exact controller errno before the result-only -EIO fallback. */
		dmaengine_pcm_notify_fault(substream, error < 0 ? error : -EIO, true);
		return;
	}
	if (dmaengine_pcm_error(prtd, 0))
		return;
	dmaengine_pcm_dma_complete(arg);
}

int snd_dmaengine_pcm_set_error_sink(struct snd_pcm_substream *substream,
		void (*sink)(struct snd_pcm_substream *, int, void *), void *arg)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	unsigned long flags;
	int ret = 0;

	if (!prtd || !prtd->checked)
		return sink ? -EOPNOTSUPP : 0;
	spin_lock_irqsave(&prtd->lifecycle_lock, flags);
	if (prtd->exposed || prtd->closing || prtd->quiescing || prtd->error_sink)
		ret = -EBUSY;
	else {
		prtd->error_sink = sink;
		prtd->error_sink_arg = arg;
	}
	spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_set_error_sink);

static int dmaengine_pcm_prepare_and_submit(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	struct dma_chan *chan = prtd->dma_chan;
	struct dma_async_tx_descriptor *desc;
	enum dma_transfer_direction direction;
	unsigned long flags = DMA_CTRL_ACK, irqflags;
	int ret;

	prtd->cookie = 0;
	ret = dmaengine_pcm_check_fault(substream);
	if (ret)
		return ret;
	if (prtd->checked) {
		ret = snd_pcm_dma_quarantine_check(substream, chan->device->dev);
		if (ret)
			return ret;
	}
	direction = snd_pcm_substream_to_dma_direction(substream);

	if (!substream->runtime->no_period_wakeup)
		flags |= DMA_PREP_INTERRUPT;

	prtd->pos = 0;
	desc = dmaengine_prep_dma_cyclic(chan,
		substream->runtime->dma_addr,
		snd_pcm_lib_buffer_bytes(substream),
		snd_pcm_lib_period_bytes(substream), direction, flags);

	if (!desc) {
		ret = dmaengine_pcm_check_fault(substream);
		return ret ? ret : -ENOMEM;
	}

	if (prtd->checked)
		desc->callback_result = dmaengine_pcm_dma_complete_result;
	else
		desc->callback = dmaengine_pcm_dma_complete;
	desc->callback_param = substream;
	prtd->cookie = dmaengine_submit(desc);
	ret = dma_submit_error(prtd->cookie);
	if (ret) {
		int exact = dmaengine_pcm_check_fault(substream);

		return exact ? exact : ret;
	}
	if (prtd->checked) {
		spin_lock_irqsave(&prtd->lifecycle_lock, irqflags);
		prtd->owner_buffer = substream->runtime->dma_buffer_p;
		prtd->owner = *prtd->owner_buffer;
		prtd->exposed = true;
		spin_unlock_irqrestore(&prtd->lifecycle_lock, irqflags);
	}

	return 0;
}

/**
 * snd_dmaengine_pcm_trigger - dmaengine based PCM trigger implementation
 * @substream: PCM substream
 * @cmd: Trigger command
 *
 * Returns 0 on success, a negative error code otherwise.
 *
 * This function can be used as the PCM trigger callback for dmaengine based PCM
 * driver implementations.
 */
int snd_dmaengine_pcm_trigger(struct snd_pcm_substream *substream, int cmd)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	struct snd_pcm_runtime *runtime = substream->runtime;
	unsigned long flags;
	bool stopping;
	int ret;

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		break;
	default:
		return -EINVAL;
	}
	stopping = cmd == SNDRV_PCM_TRIGGER_STOP ||
		(cmd == SNDRV_PCM_TRIGGER_SUSPEND && !(runtime->info & SNDRV_PCM_INFO_PAUSE));
	if (!stopping) {
		ret = dmaengine_pcm_check_fault(substream);
		if (ret)
			return ret;
	}
	ret = dmaengine_pcm_operation_begin(prtd, cmd, stopping);
	if (ret)
		return ret;
	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
		ret = dmaengine_pcm_prepare_and_submit(substream);
		if (ret)
			break;
		ret = dmaengine_pcm_check_fault(substream);
		if (ret)
			break;
		dma_async_issue_pending(prtd->dma_chan);
		ret = dmaengine_pcm_check_fault(substream);
		if (ret)
			break;
		if (prtd->checked && dmaengine_tx_status(prtd->dma_chan, prtd->cookie, NULL) == DMA_ERROR) {
			spin_lock_irqsave(&prtd->lifecycle_lock, flags);
			prtd->go_failed = true;
			spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
			ret = dmaengine_pcm_check_fault(substream);
			if (!ret)
				dmaengine_pcm_notify_fault(substream, -EIO, false);
			dmaengine_pcm_operation_end(prtd);
			return dmaengine_pcm_error(prtd, ret ? ret : -EIO);
		}
		break;
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		ret = dmaengine_resume(prtd->dma_chan);
		break;
	case SNDRV_PCM_TRIGGER_SUSPEND:
		if (runtime->info & SNDRV_PCM_INFO_PAUSE)
			ret = dmaengine_pause(prtd->dma_chan);
		else
			ret = dmaengine_terminate_async(prtd->dma_chan);
		break;
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		ret = dmaengine_pause(prtd->dma_chan);
		break;
	case SNDRV_PCM_TRIGGER_STOP:
		ret = dmaengine_terminate_async(prtd->dma_chan);
		break;
	default:
		ret = -EINVAL;
		break;
	}
	/* Validation/prep NULL has no exact I/O errno; checked obtains it later. */
	if (cmd == SNDRV_PCM_TRIGGER_START && ret < 0 && prtd->cookie >= 0) {
		dmaengine_pcm_operation_end(prtd);
		return ret;
	}
	ret = dmaengine_pcm_error(prtd, ret);
	dmaengine_pcm_operation_end(prtd);
	return ret;
}

EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_trigger);

/**
 * snd_dmaengine_pcm_pointer_no_residue - dmaengine based PCM pointer implementation
 * @substream: PCM substream
 *
 * This function is deprecated and should not be used by new drivers, as its
 * results may be unreliable.
 */
snd_pcm_uframes_t snd_dmaengine_pcm_pointer_no_residue(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	if (dmaengine_pcm_check_fault(substream))
		return SNDRV_PCM_POS_XRUN;
	return bytes_to_frames(substream->runtime, prtd->pos);
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_pointer_no_residue);

/**
 * snd_dmaengine_pcm_pointer - dmaengine based PCM pointer implementation
 * @substream: PCM substream
 *
 * This function can be used as the PCM pointer callback for dmaengine based PCM
 * driver implementations.
 */
snd_pcm_uframes_t snd_dmaengine_pcm_pointer(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);
	struct snd_pcm_runtime *runtime = substream->runtime;
	struct dma_tx_state state;
	unsigned int buf_size;
	unsigned int pos = 0;

	if (dmaengine_pcm_check_fault(substream))
		return SNDRV_PCM_POS_XRUN;
	if (dmaengine_tx_status(prtd->dma_chan, prtd->cookie, &state) == DMA_ERROR && prtd->checked) {
		if (!dmaengine_pcm_check_fault(substream))
			dmaengine_pcm_notify_fault(substream, -EIO, false);
		return SNDRV_PCM_POS_XRUN;
	}
	buf_size = snd_pcm_lib_buffer_bytes(substream);
	if (state.residue > 0 && state.residue <= buf_size)
		pos = buf_size - state.residue;

	runtime->delay = bytes_to_frames(runtime,
					 state.in_flight_bytes);

	return bytes_to_frames(runtime, pos);
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_pointer);

/**
 * snd_dmaengine_pcm_request_channel - Request channel for the dmaengine PCM
 * @filter_fn: Filter function used to request the DMA channel
 * @filter_data: Data passed to the DMA filter function
 *
 * Returns NULL or the requested DMA channel.
 *
 * This function request a DMA channel for usage with dmaengine PCM.
 */
struct dma_chan *snd_dmaengine_pcm_request_channel(dma_filter_fn filter_fn,
	void *filter_data)
{
	dma_cap_mask_t mask;

	dma_cap_zero(mask);
	dma_cap_set(DMA_SLAVE, mask);
	dma_cap_set(DMA_CYCLIC, mask);

	return dma_request_channel(mask, filter_fn, filter_data);
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_request_channel);

/**
 * snd_dmaengine_pcm_open - Open a dmaengine based PCM substream
 * @substream: PCM substream
 * @chan: DMA channel to use for data transfers
 *
 * Returns 0 on success, a negative error code otherwise.
 *
 * The function should usually be called from the pcm open callback. Note that
 * this function will use private_data field of the substream's runtime. So it
 * is not available to your pcm driver implementation.
 */
int snd_dmaengine_pcm_open(struct snd_pcm_substream *substream,
	struct dma_chan *chan)
{
	struct dmaengine_pcm_runtime_data *prtd;
	int ret;

	if (!chan)
		return -ENXIO;

	ret = dmaengine_check_open(chan);
	if (ret < 0)
		return ret;

	ret = snd_pcm_hw_constraint_integer(substream->runtime,
					    SNDRV_PCM_HW_PARAM_PERIODS);
	if (ret < 0)
		return ret;

	prtd = kzalloc(sizeof(*prtd), GFP_KERNEL);
	if (!prtd)
		return -ENOMEM;

	prtd->dma_chan = chan;
	prtd->checked = !!chan->device->device_synchronize_checked;
	spin_lock_init(&prtd->lifecycle_lock);
	atomic_set(&prtd->operations, 0);
	init_waitqueue_head(&prtd->drain_wait);
	mutex_init(&prtd->quiesce_mutex);
	if (prtd->checked) {
		prtd->quarantine = snd_pcm_dma_quarantine_alloc();
		if (!prtd->quarantine) {
			kfree(prtd);
			return -ENOMEM;
		}
	}

	substream->runtime->private_data = prtd;
	if (prtd->checked)
		substream->runtime->dma_quiesce = snd_dmaengine_pcm_quiesce;

	return 0;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_open);

/**
 * snd_dmaengine_pcm_open_request_chan - Open a dmaengine based PCM substream and request channel
 * @substream: PCM substream
 * @filter_fn: Filter function used to request the DMA channel
 * @filter_data: Data passed to the DMA filter function
 *
 * Returns 0 on success, a negative error code otherwise.
 *
 * This function will request a DMA channel using the passed filter function and
 * data. The function should usually be called from the pcm open callback. Note
 * that this function will use private_data field of the substream's runtime. So
 * it is not available to your pcm driver implementation.
 */
int snd_dmaengine_pcm_open_request_chan(struct snd_pcm_substream *substream,
	dma_filter_fn filter_fn, void *filter_data)
{
	return snd_dmaengine_pcm_open(substream,
		    snd_dmaengine_pcm_request_channel(filter_fn, filter_data));
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_open_request_chan);

/**
 * snd_dmaengine_pcm_close - Close a dmaengine based PCM substream
 * @substream: PCM substream
 */
int snd_dmaengine_pcm_close(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);

	unsigned long flags;
	int ret;

	if (!prtd)
		return 0;
	if (prtd->checked) {
		spin_lock_irqsave(&prtd->lifecycle_lock, flags);
		prtd->closing = true;
		spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	}
	ret = snd_dmaengine_pcm_quiesce(substream);
	substream->runtime->dma_quiesce = NULL;
	substream->runtime->private_data = NULL;
	snd_pcm_dma_quarantine_free(prtd->quarantine);
	kfree(prtd);

	return ret;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_close);

/**
 * snd_dmaengine_pcm_close_release_chan - Close a dmaengine based PCM
 *					  substream and release channel
 * @substream: PCM substream
 *
 * Releases the DMA channel associated with the PCM substream.
 */
int snd_dmaengine_pcm_close_release_chan(struct snd_pcm_substream *substream)
{
	struct dmaengine_pcm_runtime_data *prtd = substream_to_prtd(substream);

	unsigned long flags;
	int ret;

	if (!prtd)
		return 0;
	if (prtd->checked) {
		spin_lock_irqsave(&prtd->lifecycle_lock, flags);
		prtd->closing = true;
		spin_unlock_irqrestore(&prtd->lifecycle_lock, flags);
	}
	ret = snd_dmaengine_pcm_quiesce(substream);
	if (prtd->checked && ret < 0)
		dmaengine_pcm_failstop("channel owner cannot be released");
	dma_release_channel(prtd->dma_chan);
	substream->runtime->dma_quiesce = NULL;
	substream->runtime->private_data = NULL;
	snd_pcm_dma_quarantine_free(prtd->quarantine);
	kfree(prtd);

	return ret;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_close_release_chan);

/**
 * snd_dmaengine_pcm_refine_runtime_hwparams - Refine runtime hw params
 * @substream: PCM substream
 * @dma_data: DAI DMA data
 * @hw: PCM hw params
 * @chan: DMA channel to use for data transfers
 *
 * Returns 0 on success, a negative error code otherwise.
 *
 * This function will query DMA capability, then refine the pcm hardware
 * parameters.
 */
int snd_dmaengine_pcm_refine_runtime_hwparams(
	struct snd_pcm_substream *substream,
	struct snd_dmaengine_dai_dma_data *dma_data,
	struct snd_pcm_hardware *hw,
	struct dma_chan *chan)
{
	struct dma_slave_caps dma_caps;
	u32 addr_widths = BIT(DMA_SLAVE_BUSWIDTH_1_BYTE) |
			  BIT(DMA_SLAVE_BUSWIDTH_2_BYTES) |
			  BIT(DMA_SLAVE_BUSWIDTH_4_BYTES);
	snd_pcm_format_t i;
	int ret = 0;

	if (!hw || !chan || !dma_data)
		return -EINVAL;

	ret = dma_get_slave_caps(chan, &dma_caps);
	if (ret == 0) {
		if (dma_caps.cmd_pause && dma_caps.cmd_resume)
			hw->info |= SNDRV_PCM_INFO_PAUSE | SNDRV_PCM_INFO_RESUME;
		if (dma_caps.residue_granularity <= DMA_RESIDUE_GRANULARITY_SEGMENT)
			hw->info |= SNDRV_PCM_INFO_BATCH;

		if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK)
			addr_widths = dma_caps.dst_addr_widths;
		else
			addr_widths = dma_caps.src_addr_widths;
	}

	/*
	 * If SND_DMAENGINE_PCM_DAI_FLAG_PACK is set keep
	 * hw.formats set to 0, meaning no restrictions are in place.
	 * In this case it's the responsibility of the DAI driver to
	 * provide the supported format information.
	 */
	if (!(dma_data->flags & SND_DMAENGINE_PCM_DAI_FLAG_PACK))
		/*
		 * Prepare formats mask for valid/allowed sample types. If the
		 * dma does not have support for the given physical word size,
		 * it needs to be masked out so user space can not use the
		 * format which produces corrupted audio.
		 * In case the dma driver does not implement the slave_caps the
		 * default assumption is that it supports 1, 2 and 4 bytes
		 * widths.
		 */
		pcm_for_each_format(i) {
			int bits = snd_pcm_format_physical_width(i);

			/*
			 * Enable only samples with DMA supported physical
			 * widths
			 */
			switch (bits) {
			case 8:
			case 16:
			case 24:
			case 32:
			case 64:
				if (addr_widths & (1 << (bits / 8)))
					hw->formats |= pcm_format_to_bits(i);
				break;
			default:
				/* Unsupported types */
				break;
			}
		}

	return ret;
}
EXPORT_SYMBOL_GPL(snd_dmaengine_pcm_refine_runtime_hwparams);

MODULE_LICENSE("GPL");

/* sound/soc/rockchip/rockchip_i2s_tdm.c
 *
 * ALSA SoC Audio Layer - Rockchip I2S/TDM Controller driver
 *
 * Copyright (c) 2018 Rockchip Electronics Co. Ltd.
 * Author: Sugar Zhang <sugar.zhang@rock-chips.com>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 */

#include <linux/module.h>
#include <linux/mfd/syscon.h>
#include <linux/delay.h>
#include <linux/of_gpio.h>
#include <linux/of_device.h>
#include <linux/of_address.h>
#include <linux/clk.h>
#include <linux/clk-provider.h>
#include <linux/clk/rockchip.h>
#include <linux/pinctrl/consumer.h>
#include <linux/pm_runtime.h>
#include <linux/regmap.h>
#include <linux/reset.h>
#include <linux/spinlock.h>
#include <sound/pcm_params.h>
#include <sound/dmaengine_pcm.h>

#include "rockchip_i2s_tdm.h"
#include "rockchip_dlp.h"

#define DRV_NAME "rockchip-i2s-tdm"

#if IS_ENABLED(CONFIG_CPU_PX30) || IS_ENABLED(CONFIG_CPU_RK1808) || IS_ENABLED(CONFIG_CPU_RK3308)
#define HAVE_SYNC_RESET
#endif

#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
/*
 * Example: RK3588
 *
 * Use I2S2_2CH as Clk-Gen to serve TDM_MULTI_LANES
 *
 * I2S2_2CH ----> BCLK,I2S_LRCK --------> I2S0_8CH_TX (Slave TRCM-TXONLY)
 *     |
 *     |--------> BCLK,TDM_SYNC --------> TDM Device (Slave)
 *
 * Note:
 *
 * I2S2_2CH_MCLK: BCLK
 * I2S2_2CH_SCLK: I2S_LRCK (GPIO2_B7)
 * I2S2_2CH_LRCK: TDM_SYNC (GPIO2_C0)
 *
 */

#define CLK_MAX_COUNT				1000
#define NSAMPLES				4
#define XFER_EN					0x3
#define XFER_DIS				0x0
#define CKR_V(m, r, t)				((m - 1) << 16 | (r - 1) << 8 | (t - 1) << 0)
#define I2S_XCR_IBM_V(v)			((v) & I2S_TXCR_IBM_MASK)
#define I2S_XCR_IBM_NORMAL			I2S_TXCR_IBM_NORMAL
#define I2S_XCR_IBM_LSJM			I2S_TXCR_IBM_LSJM
#endif

#define DEFAULT_MCLK_FS				256
#define DEFAULT_FS				48000
#define CH_GRP_MAX				4  /* The max channel 8 / 2 */
#define MULTIPLEX_CH_MAX			10
#define CLK_PPM_MIN				(-1000)
#define CLK_PPM_MAX				(1000)
#define MAXBURST_PER_FIFO			8
#define WAIT_TIME_MS_MAX			10000

#define QUIRK_ALWAYS_ON				BIT(0)
#define QUIRK_HDMI_PATH				BIT(1)

struct txrx_config {
	u32 addr;
	u32 reg;
	u32 txonly;
	u32 rxonly;
};

struct rk_i2s_soc_data {
	u32 softrst_offset;
	u32 grf_reg_offset;
	u32 grf_shift;
	int config_count;
	const struct txrx_config *configs;
	int (*init)(struct device *dev, u32 addr);
};

struct rk_i2s_tdm_dev {
	struct device *dev;
	struct clk *hclk;
	struct clk *mclk_tx;
	struct clk *mclk_rx;
	/* The mclk_tx_src is parent of mclk_tx */
	struct clk *mclk_tx_src;
	/* The mclk_rx_src is parent of mclk_rx */
	struct clk *mclk_rx_src;
	/*
	 * The mclk_root0 and mclk_root1 are root parent and supplies for
	 * the different FS.
	 *
	 * e.g:
	 * mclk_root0 is VPLL0, used for FS=48000Hz
	 * mclk_root0 is VPLL1, used for FS=44100Hz
	 */
	struct clk *mclk_root0;
	struct clk *mclk_root1;
	struct regmap *regmap;
	struct regmap *grf;
	struct snd_dmaengine_dai_dma_data capture_dma_data;
	struct snd_dmaengine_dai_dma_data playback_dma_data;
	struct snd_pcm_substream *substreams[SNDRV_PCM_STREAM_LAST + 1];
	unsigned int wait_time[SNDRV_PCM_STREAM_LAST + 1];
	struct reset_control *tx_reset;
	struct reset_control *rx_reset;
	struct pinctrl *pinctrl;
	struct pinctrl_state *clk_state;
	const struct rk_i2s_soc_data *soc_data;
#ifdef HAVE_SYNC_RESET
	void __iomem *cru_base;
	int tx_reset_id;
	int rx_reset_id;
#endif
	/* Only the locked RK3568 I2S1 ordinary master path opts in. */
	bool shared_params_enabled;
	struct snd_soc_dai_params_state shared_params;
	unsigned int shared_dai_fmt, shared_prepared;
	/* Per-direction C3 reservation; epoch revoke never claims GO borrow drained. */
	unsigned int starting, starting_valid, quiet_proven;
	u64 trcm_epoch, trcm_ticket_epoch[2];
	struct snd_pcm_substream *trcm_ticket[2], *trcm_active[2];
	bool transport_live;
	bool checked_lifecycle;
	bool configuring;
	bool format_pm_release;
	bool stop_proven;
	bool irq_live;
	bool mclks_enabled;
	bool hclk_enabled;
	bool pm_enabled; /* one owned pm_runtime_enable, process teardown balances it */
	bool irq_drained;
	bool power_transition;
	bool ready;
	unsigned int stop_reads;
	void __iomem *regs;
	int irq;
	bool shutting_down;
	unsigned int started;
	int runtime_error;
	bool is_master_mode;
	bool io_multiplex;
	bool mclk_calibrate;
	bool tdm_mode;
	bool tdm_fsync_half_frame;
	bool is_dma_active[SNDRV_PCM_STREAM_LAST + 1];
	unsigned int mclk_rx_freq;
	unsigned int mclk_tx_freq;
	unsigned int mclk_root0_freq;
	unsigned int mclk_root1_freq;
	unsigned int mclk_root0_initial_freq;
	unsigned int mclk_root1_initial_freq;
	unsigned int bclk_fs;
	unsigned int clk_trcm;
	unsigned int i2s_sdis[CH_GRP_MAX];
	unsigned int i2s_sdos[CH_GRP_MAX];
	unsigned int quirks;
	unsigned int lrck_ratio;
	int clk_ppm;
	atomic_t refcount;
	spinlock_t lock; /* xfer lock */
#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
	struct snd_soc_dai *clk_src_dai;
	struct gpio_desc *i2s_lrck_gpio;
	struct gpio_desc *tdm_fsync_gpio;
	unsigned int tx_lanes;
	unsigned int rx_lanes;
	void __iomem *clk_src_base;
	bool is_tdm_multi_lanes;
#endif
};

static struct i2s_of_quirks {
	char *quirk;
	int id;
} of_quirks[] = {
	{
		.quirk = "rockchip,always-on",
		.id = QUIRK_ALWAYS_ON,
	},
	{
		.quirk = "rockchip,hdmi-path",
		.id = QUIRK_HDMI_PATH,
	},
};

static int to_ch_num(unsigned int val)
{
	int chs;

	switch (val) {
	case I2S_CHN_4:
		chs = 4;
		break;
	case I2S_CHN_6:
		chs = 6;
		break;
	case I2S_CHN_8:
		chs = 8;
		break;
	default:
		chs = 2;
		break;
	}

	return chs;
}


static int i2s_checked_error_locked(struct rk_i2s_tdm_dev *i2s_tdm, int ret);
static int i2s_checked_stop_locked(struct rk_i2s_tdm_dev *i2s_tdm, int stream, bool force);
static int i2s_checked_first_error(int first, int next);
static int i2s_checked_all_stop_locked(struct rk_i2s_tdm_dev *i2s_tdm);
static int i2s_trcm_joint_locked(struct rk_i2s_tdm_dev *i2s_tdm, int first);
static int rockchip_i2s_tdm_pinctrl_select_clk_state(struct device *dev);

static void i2s_checked_failstop(struct rk_i2s_tdm_dev *i2s_tdm,
			       const char *operation, int ret)
{
	/* Returning from remove does not preserve devres. Never warm reboot. */
	WRITE_ONCE(panic_timeout, 0);
	panic("%s: %s unsafe CPU I2S destruction (%d)", DRV_NAME, operation, ret);
}

static int i2s_checked_runtime_suspend(struct rk_i2s_tdm_dev *i2s_tdm)
{
	unsigned long flags;
	int ret = 0;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (i2s_tdm->runtime_error && !i2s_tdm->shutting_down)
		ret = i2s_tdm->runtime_error;
	else if (i2s_tdm->shared_params_enabled &&
		 (i2s_tdm->shared_params.pending || i2s_tdm->shared_params.owner[0] ||
		  i2s_tdm->shared_params.owner[1]))
		ret = -EBUSY;
	else if ((i2s_tdm->configuring && !i2s_tdm->format_pm_release) ||
		 i2s_tdm->started || i2s_tdm->starting || !i2s_tdm->stop_proven)
		ret = -EBUSY;
	if (!ret) {
		i2s_tdm->power_transition = true;
		i2s_tdm->irq_live = false;
		i2s_tdm->irq_drained = false;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (ret)
		return ret;
	if (i2s_tdm->irq > 0)
		synchronize_irq(i2s_tdm->irq);
	regcache_cache_only(i2s_tdm->regmap, true);
	if (i2s_tdm->mclks_enabled) {
		clk_disable_unprepare(i2s_tdm->mclk_tx);
		clk_disable_unprepare(i2s_tdm->mclk_rx);
		i2s_tdm->mclks_enabled = false;
	}
	ret = pinctrl_pm_select_idle_state(i2s_tdm->dev);
	if (ret) {
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		i2s_checked_error_locked(i2s_tdm, ret);
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	}
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	i2s_tdm->irq_drained = true;
	i2s_tdm->power_transition = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_checked_runtime_resume(struct rk_i2s_tdm_dev *i2s_tdm)
{
	unsigned long flags;
	bool stopping, proved;
	int ret, next;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	stopping = i2s_tdm->shutting_down;
	ret = i2s_tdm->runtime_error;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (ret && !stopping)
		return ret;
	/* PM core serializes callbacks; process destruction disables/drains it. */
	if (i2s_tdm->mclks_enabled)
		return 0;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	i2s_tdm->power_transition = true;
	i2s_tdm->irq_drained = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	ret = rockchip_i2s_tdm_pinctrl_select_clk_state(i2s_tdm->dev);
	if (ret)
		goto error;
	ret = clk_prepare_enable(i2s_tdm->mclk_tx);
	if (ret)
		goto error;
	ret = clk_prepare_enable(i2s_tdm->mclk_rx);
	if (ret)
		goto disable_tx;
	/* Own both refs before any cache replay can partially touch hardware. */
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	i2s_tdm->mclks_enabled = true;
	i2s_tdm->stop_proven = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	regcache_cache_only(i2s_tdm->regmap, false);
	/* Destruction only issues forced STOP and never replays cached START. */
	if (!stopping) {
		regcache_mark_dirty(i2s_tdm->regmap);
		ret = regcache_sync(i2s_tdm->regmap);
		if (ret)
			goto cache_error;
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		ret = i2s_checked_all_stop_locked(i2s_tdm);
		i2s_tdm->stop_proven = !ret;
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		if (ret)
			goto cache_error;
	}
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	/* Teardown may revoke the gate while this callback is acquiring clocks. */
	i2s_tdm->irq_live = !i2s_tdm->shutting_down;
	i2s_tdm->power_transition = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return 0;
cache_error:
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	/* Preserve the initiating errno even if subsequent STOP also fails. */
	i2s_checked_error_locked(i2s_tdm, ret);
	next = i2s_checked_all_stop_locked(i2s_tdm);
	proved = !next && !i2s_tdm->starting;
	i2s_tdm->stop_proven = proved;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (proved) {
		regcache_cache_only(i2s_tdm->regmap, true);
		clk_disable_unprepare(i2s_tdm->mclk_rx);
		clk_disable_unprepare(i2s_tdm->mclk_tx);
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		i2s_tdm->mclks_enabled = false;
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	}
	/* Unproved hardware keeps both refs and MMIO access for process cleanup. */
	goto latch_error;
disable_tx:
	/* RX enable failed before any MMIO/cache replay in this callback. */
	clk_disable_unprepare(i2s_tdm->mclk_tx);
error:
	regcache_cache_only(i2s_tdm->regmap, true);
latch_error:
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	i2s_tdm->irq_live = false;
	i2s_tdm->power_transition = false;
	i2s_checked_error_locked(i2s_tdm, ret);
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static void i2s_checked_pm_disable(struct rk_i2s_tdm_dev *i2s_tdm)
{
	unsigned long flags;
	bool owned;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	owned = i2s_tdm->pm_enabled;
	i2s_tdm->pm_enabled = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	/* One disable balances our one enable. It also drains queued callbacks. */
	if (owned)
		pm_runtime_disable(i2s_tdm->dev);
}

static void i2s_checked_quiesce(struct rk_i2s_tdm_dev *i2s_tdm,
			      const char *operation)
{
	unsigned long flags;
	int ret;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	/* No cross-ALSA ownership exists here; devres cannot destroy open owners. */
	ret = i2s_tdm->configuring || i2s_tdm->started || i2s_tdm->starting ||
		READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_PLAYBACK]) ||
		READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_CAPTURE]) ? -EBUSY : 0;
	if (!ret) {
		WRITE_ONCE(i2s_tdm->ready, false);
		i2s_tdm->shutting_down = true;
		i2s_tdm->irq_live = false;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (ret)
		i2s_checked_failstop(i2s_tdm, operation, ret);
	if (i2s_tdm->irq > 0)
		synchronize_irq(i2s_tdm->irq);
	i2s_checked_pm_disable(i2s_tdm);
	/* A latched PM-core callback error rejects get_sync with -EINVAL.
	 * After disable drained the core, our clock refs provide a separate,
	 * serialized process-only STOP path without a new runtime PM usage lease.
	 */
	if (i2s_tdm->hclk_enabled) {
		ret = i2s_checked_runtime_resume(i2s_tdm);
		if (ret)
			i2s_checked_failstop(i2s_tdm, operation, ret);
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		ret = i2s_checked_all_stop_locked(i2s_tdm);
		i2s_tdm->stop_proven = !ret;
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		if (ret)
			i2s_checked_failstop(i2s_tdm, operation, ret);
		ret = i2s_checked_runtime_suspend(i2s_tdm);
		if (ret)
			i2s_checked_failstop(i2s_tdm, operation, ret);
		clk_disable_unprepare(i2s_tdm->hclk);
		i2s_tdm->hclk_enabled = false;
	}
	/* This is legal only after actual STOP proof and with core disabled. */
	ret = pm_runtime_set_suspended(i2s_tdm->dev);
	if (ret)
		i2s_checked_failstop(i2s_tdm, operation, ret);
}

static void i2s_checked_probe_clock_release(void *data)
{
	struct rk_i2s_tdm_dev *i2s_tdm = data;
	unsigned long flags;
	int ret;

	/* IRQ/component devres are gone; all clock consumer handles are still alive. */
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	WRITE_ONCE(i2s_tdm->ready, false);
	i2s_tdm->shutting_down = true;
	i2s_tdm->irq_live = false;
	ret = READ_ONCE(i2s_tdm->substreams[0]) || READ_ONCE(i2s_tdm->substreams[1]) ||
		i2s_tdm->started || i2s_tdm->starting || i2s_tdm->configuring ? -EBUSY : 0;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (ret)
		i2s_checked_failstop(i2s_tdm, "probe cleanup", ret);
	/* PM disable drains pending/running PM callbacks before consumer release. */
	i2s_checked_pm_disable(i2s_tdm);
	if (i2s_tdm->mclks_enabled && !i2s_tdm->stop_proven)
		i2s_checked_failstop(i2s_tdm, "probe cleanup stop unproved", -EIO);
	if (i2s_tdm->mclks_enabled) {
		clk_disable_unprepare(i2s_tdm->mclk_tx);
		clk_disable_unprepare(i2s_tdm->mclk_rx);
		i2s_tdm->mclks_enabled = false;
	}
	if (i2s_tdm->hclk_enabled) {
		clk_disable_unprepare(i2s_tdm->hclk);
		i2s_tdm->hclk_enabled = false;
	}
}

static int i2s_tdm_runtime_suspend(struct device *dev)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);

	if (i2s_tdm->checked_lifecycle)
		return i2s_checked_runtime_suspend(i2s_tdm);

	regcache_cache_only(i2s_tdm->regmap, true);

	clk_disable_unprepare(i2s_tdm->mclk_tx);
	clk_disable_unprepare(i2s_tdm->mclk_rx);

	pinctrl_pm_select_idle_state(dev);

	return 0;
}

static int rockchip_i2s_tdm_pinctrl_select_clk_state(struct device *dev)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);

	if (IS_ERR_OR_NULL(i2s_tdm->pinctrl) || !i2s_tdm->clk_state)
		return 0;

	if (i2s_tdm->checked_lifecycle)
		return pinctrl_select_state(i2s_tdm->pinctrl, i2s_tdm->clk_state);
	pinctrl_select_state(i2s_tdm->pinctrl, i2s_tdm->clk_state);

	return 0;
}

static int i2s_tdm_runtime_resume(struct device *dev)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);
	int ret;

	if (i2s_tdm->checked_lifecycle)
		return i2s_checked_runtime_resume(i2s_tdm);

	/*
	 * pinctrl default state is invoked by ASoC framework, so,
	 * we just handle clk state here if DT assigned.
	 */
	if (i2s_tdm->is_master_mode)
		rockchip_i2s_tdm_pinctrl_select_clk_state(dev);

	ret = clk_prepare_enable(i2s_tdm->mclk_tx);
	if (ret)
		goto err_mclk_tx;

	ret = clk_prepare_enable(i2s_tdm->mclk_rx);
	if (ret)
		goto err_mclk_rx;

	regcache_cache_only(i2s_tdm->regmap, false);
	regcache_mark_dirty(i2s_tdm->regmap);
	ret = regcache_sync(i2s_tdm->regmap);
	if (ret)
		goto err_regmap;

	/*
	 * should be placed after regcache sync done to back
	 * to the slave mode and then enable clk state.
	 */
	if (!i2s_tdm->is_master_mode)
		rockchip_i2s_tdm_pinctrl_select_clk_state(dev);

	return 0;

err_regmap:
	clk_disable_unprepare(i2s_tdm->mclk_rx);
err_mclk_rx:
	clk_disable_unprepare(i2s_tdm->mclk_tx);
err_mclk_tx:
	return ret;
}


/* Protected by lock; never cleared by STOP, HW_FREE, close or PM retry. */
static int i2s_checked_error_locked(struct rk_i2s_tdm_dev *i2s_tdm, int ret)
{
	if (ret < 0 && !i2s_tdm->runtime_error) {
		i2s_tdm->runtime_error = ret;
		if (i2s_tdm->shared_params_enabled) {
			i2s_tdm->starting_valid = 0;
			if (i2s_tdm->trcm_epoch != ~(u64)0)
				i2s_tdm->trcm_epoch++;
		}
	}
	return ret;
}

static int i2s_checked_gate_locked(struct rk_i2s_tdm_dev *i2s_tdm)
{
	if (i2s_tdm->runtime_error)
		return i2s_tdm->runtime_error;
	if (i2s_tdm->shutting_down)
		return -ESHUTDOWN;
	if (i2s_tdm->configuring || i2s_tdm->power_transition || i2s_tdm->started || i2s_tdm->starting)
		return -EBUSY;
	return 0;
}


static int i2s_checked_first_error(int first, int next)
{
	return first ? first : next;
}

static int i2s_checked_clear_locked(struct rk_i2s_tdm_dev *i2s_tdm)
{
	unsigned int clr = I2S_CLR_TXC | I2S_CLR_RXC;
	unsigned int val;
	int ret;

	ret = regmap_write_bits(i2s_tdm->regmap, I2S_CLR, clr, clr);
	if (ret)
		return ret;
	ret = regmap_read_poll_timeout_atomic(i2s_tdm->regmap, I2S_CLR, val,
					      !(val & clr), 10, 100);
	if (ret != -ETIMEDOUT)
		return ret;
	ret = regmap_write_bits(i2s_tdm->regmap, I2S_CLR, clr, clr);
	if (ret)
		return ret;
	/* RK3568 has no mapped sync reset here; no reset can certify clear. */
	return regmap_read_poll_timeout_atomic(i2s_tdm->regmap, I2S_CLR, val,
					       !(val & clr), 10, 100);
}

static int i2s_checked_irq_locked(struct rk_i2s_tdm_dev *i2s_tdm,
				  int stream, bool en)
{
	unsigned int ack, mask, value;
	int ret, next;

	if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		ack = I2S_INTCR_TXUIC;
		mask = I2S_INTCR_TXUIE_MASK;
		value = I2S_INTCR_TXUIE(en);
	} else {
		ack = I2S_INTCR_RXOIC;
		mask = I2S_INTCR_RXOIE_MASK;
		value = I2S_INTCR_RXOIE(en);
	}
	if (en) {
		ret = regmap_write_bits(i2s_tdm->regmap, I2S_INTCR, ack, ack);
		if (ret)
			return ret;
		return regmap_write_bits(i2s_tdm->regmap, I2S_INTCR, mask, value);
	}
	/* Disable and acknowledge independently, even after the first failure. */
	ret = regmap_write_bits(i2s_tdm->regmap, I2S_INTCR, mask, 0);
	next = regmap_write_bits(i2s_tdm->regmap, I2S_INTCR, ack, ack);
	return i2s_checked_first_error(ret, next);
}

static int i2s_checked_dma_locked(struct rk_i2s_tdm_dev *i2s_tdm,
				  int stream, bool en)
{
	unsigned int mask, value;
	int ret = 0, next;

	if (!en)
		ret = i2s_checked_irq_locked(i2s_tdm, stream, false);
	if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		mask = I2S_DMACR_TDE_MASK;
		value = I2S_DMACR_TDE(en);
	} else {
		mask = I2S_DMACR_RDE_MASK;
		value = I2S_DMACR_RDE(en);
	}
	next = regmap_write_bits(i2s_tdm->regmap, I2S_DMACR, mask, value);
	ret = i2s_checked_first_error(ret, next);
	if (en && !ret) {
		if (stream == SNDRV_PCM_STREAM_PLAYBACK)
			udelay(1);
		ret = i2s_checked_irq_locked(i2s_tdm, stream, true);
	}
	return ret;
}

/* All transport/ticket state below is protected by the CPU lock. */
static int i2s_trcm_joint_locked(struct rk_i2s_tdm_dev *i2s_tdm, int first)
{
	int ret, next;

	if (first < 0)
		i2s_checked_error_locked(i2s_tdm, first);
	/* Revoke admission, but an in-flight C3 GO borrow survives until its STOP. */
	i2s_tdm->starting_valid = 0;
	ret = i2s_checked_dma_locked(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK, false);
	next = i2s_checked_dma_locked(i2s_tdm, SNDRV_PCM_STREAM_CAPTURE, false);
	ret = i2s_checked_first_error(ret, next);
	next = regmap_write_bits(i2s_tdm->regmap, I2S_XFER,
		I2S_XFER_TXS_MASK | I2S_XFER_RXS_MASK,
		I2S_XFER_TXS_STOP | I2S_XFER_RXS_STOP);
	ret = i2s_checked_first_error(ret, next);
	udelay(150);
	next = i2s_checked_clear_locked(i2s_tdm);
	ret = i2s_checked_first_error(ret, next);
	if (!ret) {
		i2s_tdm->stop_reads++;
		if ((readl(i2s_tdm->regs + I2S_XFER) & (I2S_XFER_TXS_MASK | I2S_XFER_RXS_MASK)) ||
		    (readl(i2s_tdm->regs + I2S_DMACR) & (I2S_DMACR_TDE_MASK | I2S_DMACR_RDE_MASK)) ||
		    (readl(i2s_tdm->regs + I2S_INTCR) & (I2S_INTCR_TXUIE_MASK | I2S_INTCR_RXOIE_MASK)))
			ret = -EIO;
	}
	if (!ret) {
		i2s_tdm->started = 0;
		i2s_tdm->trcm_active[0] = i2s_tdm->trcm_active[1] = NULL;
		i2s_tdm->transport_live = false;
		i2s_tdm->quiet_proven = BIT(0) | BIT(1);
	} else {
		i2s_tdm->quiet_proven = 0;
	}
	i2s_tdm->stop_proven = !ret && !i2s_tdm->starting;
	i2s_checked_error_locked(i2s_tdm, ret);
	return i2s_checked_first_error(first, ret);
}

static int i2s_checked_all_stop_locked(struct rk_i2s_tdm_dev *i2s_tdm)
{
	int ret, next;

	if (i2s_tdm->shared_params_enabled)
		return i2s_trcm_joint_locked(i2s_tdm, 0);
	ret = i2s_checked_stop_locked(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK, true);
	next = i2s_checked_stop_locked(i2s_tdm, SNDRV_PCM_STREAM_CAPTURE, true);
	return i2s_checked_first_error(ret, next);
}

static int i2s_trcm_quiet_locked(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	unsigned int dmask = stream == SNDRV_PCM_STREAM_PLAYBACK ?
		I2S_DMACR_TDE_MASK : I2S_DMACR_RDE_MASK;
	unsigned int imask = stream == SNDRV_PCM_STREAM_PLAYBACK ?
		I2S_INTCR_TXUIE_MASK : I2S_INTCR_RXOIE_MASK;
	int ret = i2s_checked_dma_locked(i2s_tdm, stream, false);

	if (!ret && ((readl(i2s_tdm->regs + I2S_DMACR) & dmask) ||
		     (readl(i2s_tdm->regs + I2S_INTCR) & imask)))
		ret = -EIO;
	if (!ret)
		i2s_tdm->quiet_proven |= BIT(stream);
	else
		i2s_tdm->quiet_proven &= ~BIT(stream);
	return ret;
}

static int i2s_trcm_admit_locked(struct rk_i2s_tdm_dev *i2s_tdm,
			       struct snd_pcm_substream *ss)
{
	unsigned int bit;
	int stream = ss->stream;

	if (stream != SNDRV_PCM_STREAM_PLAYBACK && stream != SNDRV_PCM_STREAM_CAPTURE)
		return -EINVAL;
	bit = BIT(stream);
	if (i2s_tdm->runtime_error)
		return i2s_tdm->runtime_error;
	if (i2s_tdm->shutting_down)
		return -ESHUTDOWN;
	if (!READ_ONCE(i2s_tdm->ready))
		return -EAGAIN;
	if (i2s_tdm->configuring || i2s_tdm->power_transition || i2s_tdm->shared_params.pending ||
	    ((i2s_tdm->started | i2s_tdm->starting) & bit))
		return -EBUSY;
	if (i2s_tdm->substreams[stream] != ss || i2s_tdm->shared_params.owner[stream] != ss ||
	    !(i2s_tdm->shared_prepared & bit))
		return -EINVAL;
	if (!i2s_tdm->transport_live && !i2s_tdm->stop_proven)
		return -EBUSY;
	if (!i2s_tdm->trcm_epoch)
		i2s_tdm->trcm_epoch = 1;
	i2s_tdm->trcm_ticket[stream] = ss;
	i2s_tdm->trcm_ticket_epoch[stream] = i2s_tdm->trcm_epoch;
	i2s_tdm->starting |= bit;
	i2s_tdm->starting_valid |= bit;
	i2s_tdm->irq_drained = false;
	return 0;
}

static int i2s_trcm_commit_locked(struct rk_i2s_tdm_dev *i2s_tdm,
				struct snd_pcm_substream *ss)
{
	unsigned int bit = BIT(ss->stream);
	bool first;
	int ret;

	if (i2s_tdm->runtime_error)
		return i2s_tdm->runtime_error;
	if (i2s_tdm->shutting_down)
		return -ESHUTDOWN;
	if (!READ_ONCE(i2s_tdm->ready))
		return -EAGAIN;
	if (i2s_tdm->configuring || i2s_tdm->power_transition || i2s_tdm->shared_params.pending)
		return -EBUSY;
	if (!(i2s_tdm->starting & i2s_tdm->starting_valid & bit) ||
	    i2s_tdm->trcm_ticket[ss->stream] != ss ||
	    i2s_tdm->trcm_ticket_epoch[ss->stream] != i2s_tdm->trcm_epoch ||
	    i2s_tdm->substreams[ss->stream] != ss ||
	    i2s_tdm->shared_params.owner[ss->stream] != ss ||
	    !(i2s_tdm->shared_prepared & bit))
		return -EINVAL;
	first = !i2s_tdm->transport_live;
	if (first && !i2s_tdm->stop_proven)
		return -EBUSY;
	i2s_tdm->stop_proven = false;
	i2s_tdm->quiet_proven &= ~bit;
	/* Own access before the first request write may partly execute. */
	i2s_tdm->transport_live = true;
	ret = i2s_checked_dma_locked(i2s_tdm, ss->stream, true);
	if (!ret && first)
		ret = regmap_write_bits(i2s_tdm->regmap, I2S_XFER,
			I2S_XFER_TXS_MASK | I2S_XFER_RXS_MASK,
			I2S_XFER_TXS_START | I2S_XFER_RXS_START);
	if (ret) {
		i2s_trcm_joint_locked(i2s_tdm, ret);
		return ret;
	}
	i2s_tdm->started |= bit;
	i2s_tdm->trcm_active[ss->stream] = ss;
	i2s_tdm->starting &= ~bit;
	i2s_tdm->starting_valid &= ~bit;
	i2s_tdm->trcm_ticket[ss->stream] = NULL;
	i2s_tdm->trcm_ticket_epoch[ss->stream] = 0;
	return 0;
}

static int i2s_trcm_stop_locked(struct rk_i2s_tdm_dev *i2s_tdm,
			      struct snd_pcm_substream *ss, bool component)
{
	unsigned int bit = BIT(ss->stream);
	bool ticket = (i2s_tdm->starting & bit) && i2s_tdm->trcm_ticket[ss->stream] == ss;
	bool active = (i2s_tdm->started & bit) && i2s_tdm->trcm_active[ss->stream] == ss;
	int ret;

	/* Component rollback owns only its published ticket; failed admission may own nothing. */
	if (component && !ticket)
		return 0;
	if (!ticket && !active)
		return 0;
	if (i2s_tdm->substreams[ss->stream] != ss)
		return -EINVAL;
	i2s_tdm->starting_valid &= ~bit;
	ret = i2s_trcm_quiet_locked(i2s_tdm, ss->stream);
	if (ret)
		i2s_trcm_joint_locked(i2s_tdm, ret);
	if (!ret) {
		i2s_tdm->started &= ~bit;
		i2s_tdm->trcm_active[ss->stream] = NULL;
	}
	if (component && ticket) {
		/* C3 rollback has returned from its GO callback; same-ss ALSA is serialized. */
		i2s_tdm->starting &= ~bit;
		i2s_tdm->trcm_ticket[ss->stream] = NULL;
		i2s_tdm->trcm_ticket_epoch[ss->stream] = 0;
	}
	if (!(i2s_tdm->started | i2s_tdm->starting)) {
		int next = i2s_trcm_joint_locked(i2s_tdm, 0);
		ret = i2s_checked_first_error(ret, next);
	} else {
		i2s_tdm->stop_proven = false;
	}
	return ret;
}

static int i2s_trcm_trigger(struct rk_i2s_tdm_dev *i2s_tdm,
			  struct snd_pcm_substream *ss, int cmd)
{
	unsigned long flags;
	int ret;

	if (ss->stream != SNDRV_PCM_STREAM_PLAYBACK && ss->stream != SNDRV_PCM_STREAM_CAPTURE)
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		ret = i2s_trcm_commit_locked(i2s_tdm, ss);
		break;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		ret = i2s_trcm_stop_locked(i2s_tdm, ss, false);
		break;
	default:
		ret = -EINVAL;
		break;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_checked_stop_locked(struct rk_i2s_tdm_dev *i2s_tdm,
				   int stream, bool force)
{
	unsigned int bit = BIT(stream);
	int ret, next;

	if (!(i2s_tdm->started & bit) && !force)
		return 0;
	if (i2s_tdm->started & ~bit)
		return -EBUSY;
	ret = i2s_checked_dma_locked(i2s_tdm, stream, false);
	next = i2s_checked_dma_locked(i2s_tdm, SNDRV_PCM_STREAM_LAST - stream, false);
	ret = i2s_checked_first_error(ret, next);
	/* FLAT cache is updated before failed MMIO. Always force the STOP write. */
	next = regmap_write_bits(i2s_tdm->regmap, I2S_XFER,
				 I2S_XFER_TXS_MASK | I2S_XFER_RXS_MASK,
				 I2S_XFER_TXS_STOP | I2S_XFER_RXS_STOP);
	ret = i2s_checked_first_error(ret, next);
	udelay(150);
	next = i2s_checked_clear_locked(i2s_tdm);
	ret = i2s_checked_first_error(ret, next);
	if (!ret) {
		/* Direct MMIO readback bypasses FLAT cache without a global bypass toggle. */
		i2s_tdm->stop_reads++;
		if ((readl(i2s_tdm->regs + I2S_XFER) & (I2S_XFER_TXS_MASK | I2S_XFER_RXS_MASK)) ||
		    (readl(i2s_tdm->regs + I2S_DMACR) & (I2S_DMACR_TDE_MASK | I2S_DMACR_RDE_MASK)) ||
		    (readl(i2s_tdm->regs + I2S_INTCR) & (I2S_INTCR_TXUIE_MASK | I2S_INTCR_RXOIE_MASK)))
			ret = -EIO;
	}
	if (!ret) {
		i2s_tdm->started &= ~bit;
		i2s_tdm->stop_proven = true;
	} else {
		i2s_tdm->stop_proven = false;
	}
	i2s_checked_error_locked(i2s_tdm, ret);
	return ret;
}

static int i2s_checked_start_locked(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	unsigned int bit = BIT(stream);
	int ret;

	if (i2s_tdm->runtime_error)
		return i2s_tdm->runtime_error;
	if (i2s_tdm->shutting_down)
		return -ESHUTDOWN;
	if (!READ_ONCE(i2s_tdm->ready))
		return -EAGAIN;
	if (i2s_tdm->configuring || i2s_tdm->power_transition)
		return -EBUSY;
	if (i2s_tdm->shared_params_enabled &&
	    (i2s_tdm->shared_params.owner[stream] != i2s_tdm->substreams[stream] ||
	     !i2s_tdm->shared_params.owner[stream] || !(i2s_tdm->shared_prepared & bit)))
		return -EINVAL;
	if (i2s_tdm->started & bit)
		return 0;
	if (i2s_tdm->started || !i2s_tdm->stop_proven)
		return -EBUSY;
	i2s_tdm->irq_drained = false;
	i2s_tdm->stop_proven = false;
	ret = i2s_checked_dma_locked(i2s_tdm, stream, true);
	if (!ret)
		ret = regmap_write_bits(i2s_tdm->regmap, I2S_XFER,
					 I2S_XFER_TXS_MASK | I2S_XFER_RXS_MASK,
					 I2S_XFER_TXS_START | I2S_XFER_RXS_START);
	if (ret) {
		i2s_checked_error_locked(i2s_tdm, ret);
		/* Keep the START errno; STOP still attempts every independent stage. */
		i2s_checked_stop_locked(i2s_tdm, stream, true);
		return ret;
	}
	i2s_tdm->started = bit;
	return 0;
}

static int i2s_checked_trigger(struct rk_i2s_tdm_dev *i2s_tdm, int stream, int cmd)
{
	unsigned long flags;
	int ret;

	if (stream != SNDRV_PCM_STREAM_PLAYBACK && stream != SNDRV_PCM_STREAM_CAPTURE)
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		ret = i2s_checked_start_locked(i2s_tdm, stream);
		break;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		ret = i2s_checked_stop_locked(i2s_tdm, stream, false);
		break;
	default:
		ret = -EINVAL;
		break;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static inline struct rk_i2s_tdm_dev *to_info(struct snd_soc_dai *dai)
{
	return snd_soc_dai_get_drvdata(dai);
}

static inline bool is_stream_active(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	unsigned int val;

	regmap_read(i2s_tdm->regmap, I2S_XFER, &val);

	if (stream == SNDRV_PCM_STREAM_PLAYBACK)
		return (val & I2S_XFER_TXS_START);
	else
		return (val & I2S_XFER_RXS_START);
}

static inline bool is_dma_active(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	unsigned int val;

	regmap_read(i2s_tdm->regmap, I2S_DMACR, &val);

	if (stream == SNDRV_PCM_STREAM_PLAYBACK)
		return (val & I2S_DMACR_TDE_MASK);
	else
		return (val & I2S_DMACR_RDE_MASK);
}

#ifdef HAVE_SYNC_RESET
#if defined(CONFIG_ARM) && !defined(writeq)
static inline void __raw_writeq(u64 val, volatile void __iomem *addr)
{
	asm volatile("strd %0, %H0, [%1]" : : "r" (val), "r" (addr));
}
#define writeq(v,c) ({ __iowmb(); __raw_writeq((__force u64) cpu_to_le64(v), c); })
#endif

static void rockchip_i2s_tdm_reset_assert(struct rk_i2s_tdm_dev *i2s_tdm)
{
	int tx_bank, rx_bank, tx_offset, rx_offset, tx_id, rx_id;
	void __iomem *cru_reset, *addr;
	unsigned long flags;
	u64 val;

	if (!i2s_tdm->cru_base || !i2s_tdm->soc_data || !i2s_tdm->is_master_mode)
		return;

	tx_id = i2s_tdm->tx_reset_id;
	rx_id = i2s_tdm->rx_reset_id;
	if (tx_id < 0 || rx_id < 0)
		return;

	tx_bank = tx_id / 16;
	tx_offset = tx_id % 16;
	rx_bank = rx_id / 16;
	rx_offset = rx_id % 16;

	dev_dbg(i2s_tdm->dev,
		"tx_bank: %d, rx_bank: %d,tx_offset: %d, rx_offset: %d\n",
		tx_bank, rx_bank, tx_offset, rx_offset);

	cru_reset = i2s_tdm->cru_base + i2s_tdm->soc_data->softrst_offset;

	switch (abs(tx_bank - rx_bank)) {
	case 0:
		writel(BIT(tx_offset) | BIT(rx_offset) |
		       (BIT(tx_offset) << 16) | (BIT(rx_offset) << 16),
		       cru_reset + (tx_bank * 4));
		break;
	case 1:
		if (tx_bank < rx_bank) {
			val = BIT(rx_offset) | (BIT(rx_offset) << 16);
			val <<= 32;
			val |= BIT(tx_offset) | (BIT(tx_offset) << 16);
			addr = cru_reset + (tx_bank * 4);
		} else {
			val = BIT(tx_offset) | (BIT(tx_offset) << 16);
			val <<= 32;
			val |= BIT(rx_offset) | (BIT(rx_offset) << 16);
			addr = cru_reset + (rx_bank * 4);
		}

		if (IS_ALIGNED((uintptr_t)addr, 8)) {
			writeq(val, addr);
			break;
		}
		fallthrough;
	default:
		local_irq_save(flags);
		writel(BIT(tx_offset) | (BIT(tx_offset) << 16),
		       cru_reset + (tx_bank * 4));
		writel(BIT(rx_offset) | (BIT(rx_offset) << 16),
		       cru_reset + (rx_bank * 4));
		local_irq_restore(flags);
		break;
	}
	/* delay for reset assert done */
	udelay(10);
}

static void rockchip_i2s_tdm_reset_deassert(struct rk_i2s_tdm_dev *i2s_tdm)
{
	int tx_bank, rx_bank, tx_offset, rx_offset, tx_id, rx_id;
	void __iomem *cru_reset, *addr;
	unsigned long flags;
	u64 val;

	if (!i2s_tdm->cru_base || !i2s_tdm->soc_data || !i2s_tdm->is_master_mode)
		return;

	tx_id = i2s_tdm->tx_reset_id;
	rx_id = i2s_tdm->rx_reset_id;
	if (tx_id < 0 || rx_id < 0)
		return;

	tx_bank = tx_id / 16;
	tx_offset = tx_id % 16;
	rx_bank = rx_id / 16;
	rx_offset = rx_id % 16;

	dev_dbg(i2s_tdm->dev,
		"tx_bank: %d, rx_bank: %d,tx_offset: %d, rx_offset: %d\n",
		tx_bank, rx_bank, tx_offset, rx_offset);

	cru_reset = i2s_tdm->cru_base + i2s_tdm->soc_data->softrst_offset;

	switch (abs(tx_bank - rx_bank)) {
	case 0:
		writel((BIT(tx_offset) << 16) | (BIT(rx_offset) << 16),
		       cru_reset + (tx_bank * 4));
		break;
	case 1:
		if (tx_bank < rx_bank) {
			val = (BIT(rx_offset) << 16);
			val <<= 32;
			val |= (BIT(tx_offset) << 16);
			addr = cru_reset + (tx_bank * 4);
		} else {
			val = (BIT(tx_offset) << 16);
			val <<= 32;
			val |= (BIT(rx_offset) << 16);
			addr = cru_reset + (rx_bank * 4);
		}

		if (IS_ALIGNED((uintptr_t)addr, 8)) {
			writeq(val, addr);
			break;
		}
		fallthrough;
	default:
		local_irq_save(flags);
		writel((BIT(tx_offset) << 16),
		       cru_reset + (tx_bank * 4));
		writel((BIT(rx_offset) << 16),
		       cru_reset + (rx_bank * 4));
		local_irq_restore(flags);
		break;
	}
	/* delay for reset deassert done */
	udelay(10);
}

/*
 * make sure both tx and rx are reset at the same time for sync lrck
 * when clk_trcm > 0
 */
static void rockchip_i2s_tdm_sync_reset(struct rk_i2s_tdm_dev *i2s_tdm)
{
	rockchip_i2s_tdm_reset_assert(i2s_tdm);
	rockchip_i2s_tdm_reset_deassert(i2s_tdm);
}
#else
static inline void rockchip_i2s_tdm_reset_assert(struct rk_i2s_tdm_dev *i2s_tdm)
{
}
static inline void rockchip_i2s_tdm_reset_deassert(struct rk_i2s_tdm_dev *i2s_tdm)
{
}
static inline void rockchip_i2s_tdm_sync_reset(struct rk_i2s_tdm_dev *i2s_tdm)
{
}
#endif

static void rockchip_i2s_tdm_reset(struct reset_control *rc)
{
	if (IS_ERR_OR_NULL(rc))
		return;

	reset_control_assert(rc);
	/* delay for reset assert done */
	udelay(10);
	reset_control_deassert(rc);
	/* delay for reset deassert done */
	udelay(10);
}

static int rockchip_i2s_tdm_clear(struct rk_i2s_tdm_dev *i2s_tdm,
				  unsigned int clr)
{
	struct reset_control *rst = NULL;
	unsigned int val = 0;
	int ret = 0;

	switch (clr) {
	case I2S_CLR_TXC:
		rst = i2s_tdm->tx_reset;
		break;
	case I2S_CLR_RXC:
		rst = i2s_tdm->rx_reset;
		break;
	case I2S_CLR_TXC | I2S_CLR_RXC:
		break;
	default:
		return -EINVAL;
	}

	regmap_update_bits(i2s_tdm->regmap, I2S_CLR, clr, clr);
	ret = regmap_read_poll_timeout_atomic(i2s_tdm->regmap, I2S_CLR, val,
					      !(val & clr), 10, 100);
	if (ret == 0)
		return 0;

	/*
	 * Workaround for FIFO clear on SLAVE mode:
	 *
	 * A Suggest to do reset hclk domain and then do mclk
	 *   domain, especially for SLAVE mode without CLK in.
	 *   at last, recovery regmap config.
	 *
	 * B Suggest to switch to MASTER, and then do FIFO clr,
	 *   at last, bring back to SLAVE.
	 *
	 * Now we choose plan B here.
	 */
	if (!i2s_tdm->is_master_mode)
		regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				   I2S_CKR_MSS_MASK, I2S_CKR_MSS_MASTER);
	regmap_update_bits(i2s_tdm->regmap, I2S_CLR, clr, clr);
	ret = regmap_read_poll_timeout_atomic(i2s_tdm->regmap, I2S_CLR, val,
					      !(val & clr), 10, 100);
	if (!i2s_tdm->is_master_mode)
		regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				   I2S_CKR_MSS_MASK, I2S_CKR_MSS_SLAVE);

	if (ret < 0) {
		dev_warn(i2s_tdm->dev, "failed to clear %u on %s mode\n",
			 clr, i2s_tdm->is_master_mode ? "master" : "slave");
		goto reset;
	}

	return 0;

reset:
	if (i2s_tdm->clk_trcm)
		rockchip_i2s_tdm_sync_reset(i2s_tdm);
	else
		rockchip_i2s_tdm_reset(rst);

	return 0;
}

/*
 * HDMI controller ignores the first FRAME_SYNC cycle, Lost one frame is no big deal
 * for LPCM, but it does matter for Bitstream (NLPCM/HBR), So, padding one frame
 * before xfer the real data to fix it.
 */
static void rockchip_i2s_tdm_tx_fifo_padding(struct rk_i2s_tdm_dev *i2s_tdm, bool en)
{
	unsigned int val, w, c, i;

	if (!en)
		return;

	regmap_read(i2s_tdm->regmap, I2S_TXCR, &val);
	w = ((val & I2S_TXCR_VDW_MASK) >> I2S_TXCR_VDW_SHIFT) + 1;
	c = to_ch_num(val & I2S_TXCR_CSR_MASK) * w / 32;

	for (i = 0; i < c; i++)
		regmap_write(i2s_tdm->regmap, I2S_TXDR, 0x0);
}

static void rockchip_i2s_tdm_fifo_xrun_detect(struct rk_i2s_tdm_dev *i2s_tdm,
					      int stream, bool en)
{
	if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		/* clear irq status which was asserted before TXUIE enabled */
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_TXUIC, I2S_INTCR_TXUIC);
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_TXUIE_MASK,
				   I2S_INTCR_TXUIE(en));
	} else {
		/* clear irq status which was asserted before RXOIE enabled */
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_RXOIC, I2S_INTCR_RXOIC);
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_RXOIE_MASK,
				   I2S_INTCR_RXOIE(en));
	}
}

static void rockchip_i2s_tdm_dma_ctrl(struct rk_i2s_tdm_dev *i2s_tdm,
				      int stream, bool en)
{
	if (!en)
		rockchip_i2s_tdm_fifo_xrun_detect(i2s_tdm, stream, 0);

	if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		if (i2s_tdm->quirks & QUIRK_HDMI_PATH)
			rockchip_i2s_tdm_tx_fifo_padding(i2s_tdm, en);

		regmap_update_bits(i2s_tdm->regmap, I2S_DMACR,
				   I2S_DMACR_TDE_MASK,
				   I2S_DMACR_TDE(en));
		/*
		 * Explicitly delay 1 usec for dma to fill FIFO,
		 * though there was a implied HW delay that around
		 * half LRCK cycle (e.g. 2.6us@192k) from XFER-start
		 * to FIFO-pop.
		 *
		 * 1 usec is enough to fill at lease 4 entry each FIFO
		 * @192k 8ch 32bit situation.
		 */
		udelay(1);
	} else {
		regmap_update_bits(i2s_tdm->regmap, I2S_DMACR,
				   I2S_DMACR_RDE_MASK,
				   I2S_DMACR_RDE(en));
	}

	if (en)
		rockchip_i2s_tdm_fifo_xrun_detect(i2s_tdm, stream, 1);
}

#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
static const char * const tx_lanes_text[] = { "Auto", "SDOx1", "SDOx2", "SDOx3", "SDOx4" };
static const char * const rx_lanes_text[] = { "Auto", "SDIx1", "SDIx2", "SDIx3", "SDIx4" };
static const struct soc_enum tx_lanes_enum =
	SOC_ENUM_SINGLE_EXT(ARRAY_SIZE(tx_lanes_text), tx_lanes_text);
static const struct soc_enum rx_lanes_enum =
	SOC_ENUM_SINGLE_EXT(ARRAY_SIZE(rx_lanes_text), rx_lanes_text);

static int rockchip_i2s_tdm_tx_lanes_get(struct snd_kcontrol *kcontrol,
					 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);

	ucontrol->value.enumerated.item[0] = i2s_tdm->tx_lanes;

	return 0;
}

static int rockchip_i2s_tdm_tx_lanes_put(struct snd_kcontrol *kcontrol,
					 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);
	int num;

	num = ucontrol->value.enumerated.item[0];
	if (num >= ARRAY_SIZE(tx_lanes_text))
		return -EINVAL;

	i2s_tdm->tx_lanes = num;

	return 1;
}

static int rockchip_i2s_tdm_rx_lanes_get(struct snd_kcontrol *kcontrol,
					 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);

	ucontrol->value.enumerated.item[0] = i2s_tdm->rx_lanes;

	return 0;
}

static int rockchip_i2s_tdm_rx_lanes_put(struct snd_kcontrol *kcontrol,
					 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);
	int num;

	num = ucontrol->value.enumerated.item[0];
	if (num >= ARRAY_SIZE(rx_lanes_text))
		return -EINVAL;

	i2s_tdm->rx_lanes = num;

	return 1;
}

static int rockchip_i2s_tdm_get_lanes(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	unsigned int lanes = 1;

	if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		if (i2s_tdm->tx_lanes)
			lanes = i2s_tdm->tx_lanes;
	} else {
		if (i2s_tdm->rx_lanes)
			lanes = i2s_tdm->rx_lanes;
	}

	return lanes;
}

static struct snd_soc_dai *rockchip_i2s_tdm_find_dai(struct device_node *np)
{
	struct snd_soc_dai_link_component dai_component = { 0 };

	dai_component.of_node = np;

	return snd_soc_find_dai_with_mutex(&dai_component);
}

static int rockchip_i2s_tdm_multi_lanes_set_clk(struct snd_pcm_substream *substream,
						struct snd_pcm_hw_params *params,
						struct snd_soc_dai *cpu_dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(cpu_dai);
	struct snd_soc_dai *dai = i2s_tdm->clk_src_dai;
	unsigned int div, mclk_rate;
	unsigned int lanes, ch_per_lane;

	lanes = rockchip_i2s_tdm_get_lanes(i2s_tdm, substream->stream);
	ch_per_lane = params_channels(params) / lanes;
	mclk_rate = ch_per_lane * params_rate(params) * 32;
	div = ch_per_lane / 2;

	/* Do nothing when use external clk src */
	if (dai && dai->driver->ops) {
		if (dai->driver->ops->set_sysclk)
			dai->driver->ops->set_sysclk(dai, substream->stream, mclk_rate, 0);

		writel(XFER_DIS, i2s_tdm->clk_src_base + I2S_XFER);
		writel(CKR_V(64, div, div), i2s_tdm->clk_src_base + I2S_CKR);
		writel(XFER_EN, i2s_tdm->clk_src_base + I2S_XFER);
	}

	i2s_tdm->lrck_ratio = div;
	i2s_tdm->mclk_tx_freq = mclk_rate;
	i2s_tdm->mclk_rx_freq = mclk_rate;

	return 0;
}

static inline int tdm_multi_lanes_clk_assert_h(const struct gpio_desc *desc)
{
	int cnt = CLK_MAX_COUNT;

	while (gpiod_get_raw_value(desc) && --cnt)
		;

	return cnt;
}

static inline int tdm_multi_lanes_clk_assert_l(const struct gpio_desc *desc)
{
	int cnt = CLK_MAX_COUNT;

	while (!gpiod_get_raw_value(desc) && --cnt)
		;

	return cnt;
}

static inline bool rockchip_i2s_tdm_clk_valid(struct rk_i2s_tdm_dev *i2s_tdm)
{
	int dc_h = CLK_MAX_COUNT, dc_l = CLK_MAX_COUNT;

	/*
	 * TBD: optimize debounce and get value
	 *
	 * debounce at least one cycle found, otherwise, the clk ref maybe
	 * not on the fly.
	 */

	/* check HIGH-Level */
	dc_h = tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);
	if (!dc_h)
		return false;

	/* check LOW-Level */
	dc_l = tdm_multi_lanes_clk_assert_l(i2s_tdm->i2s_lrck_gpio);
	if (!dc_l)
		return false;

	/* check HIGH-Level */
	dc_h = tdm_multi_lanes_clk_assert_h(i2s_tdm->tdm_fsync_gpio);
	if (!dc_h)
		return false;

	/* check LOW-Level */
	dc_l = tdm_multi_lanes_clk_assert_l(i2s_tdm->tdm_fsync_gpio);
	if (!dc_l)
		return false;

	return true;
}

static void __maybe_unused rockchip_i2s_tdm_gpio_clk_meas(struct rk_i2s_tdm_dev *i2s_tdm,
							  const struct gpio_desc *desc,
							  const char *name)
{
	int h[NSAMPLES], l[NSAMPLES], i;

	dev_dbg(i2s_tdm->dev, "%s:\n", name);

	if (!rockchip_i2s_tdm_clk_valid(i2s_tdm))
		return;

	for (i = 0; i < NSAMPLES; i++) {
		h[i] = tdm_multi_lanes_clk_assert_h(desc);
		l[i] = tdm_multi_lanes_clk_assert_l(desc);
	}

	for (i = 0; i < NSAMPLES; i++)
		dev_dbg(i2s_tdm->dev, "H[%d]: %2d, L[%d]: %2d\n",
			i, CLK_MAX_COUNT - h[i], i, CLK_MAX_COUNT - l[i]);
}

static int rockchip_i2s_tdm_multi_lanes_start(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	unsigned int tdm_h = 0, tdm_l = 0, i2s_h = 0, i2s_l = 0;
	unsigned int msk, val, reg, fmt;
	unsigned long flags;

	if (!i2s_tdm->tdm_fsync_gpio || !i2s_tdm->i2s_lrck_gpio)
		return -ENOSYS;

	if (i2s_tdm->lrck_ratio != 4 && i2s_tdm->lrck_ratio != 8)
		return -EINVAL;

	if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		msk = I2S_XFER_TXS_MASK;
		val = I2S_XFER_TXS_START;
		reg = I2S_TXCR;
	} else {
		msk = I2S_XFER_RXS_MASK;
		val = I2S_XFER_RXS_START;
		reg = I2S_RXCR;
	}

	regmap_read(i2s_tdm->regmap, reg, &fmt);
	fmt = I2S_XCR_IBM_V(fmt);

	local_irq_save(flags);

	if (!rockchip_i2s_tdm_clk_valid(i2s_tdm)) {
		local_irq_restore(flags);
		dev_err(i2s_tdm->dev, "Invalid LRCK / FSYNC measured by ref IO\n");
		return -EINVAL;
	}

	switch (fmt) {
	case I2S_XCR_IBM_NORMAL:
		tdm_h = tdm_multi_lanes_clk_assert_h(i2s_tdm->tdm_fsync_gpio);
		tdm_l = tdm_multi_lanes_clk_assert_l(i2s_tdm->tdm_fsync_gpio);

		if (i2s_tdm->lrck_ratio == 8) {
			tdm_multi_lanes_clk_assert_l(i2s_tdm->i2s_lrck_gpio);
			tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);
			tdm_multi_lanes_clk_assert_l(i2s_tdm->i2s_lrck_gpio);
			tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);
		}

		i2s_l = tdm_multi_lanes_clk_assert_l(i2s_tdm->i2s_lrck_gpio);

		if (stream == SNDRV_PCM_STREAM_CAPTURE)
			i2s_h = tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);
		break;
	case I2S_XCR_IBM_LSJM:
		tdm_l = tdm_multi_lanes_clk_assert_l(i2s_tdm->tdm_fsync_gpio);
		tdm_h = tdm_multi_lanes_clk_assert_h(i2s_tdm->tdm_fsync_gpio);

		if (i2s_tdm->lrck_ratio == 8) {
			tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);
			tdm_multi_lanes_clk_assert_l(i2s_tdm->i2s_lrck_gpio);
			tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);
			tdm_multi_lanes_clk_assert_l(i2s_tdm->i2s_lrck_gpio);
		}

		tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);

		i2s_l = tdm_multi_lanes_clk_assert_l(i2s_tdm->i2s_lrck_gpio);
		i2s_h = tdm_multi_lanes_clk_assert_h(i2s_tdm->i2s_lrck_gpio);
		break;
	default:
		local_irq_restore(flags);
		return -EINVAL;
	}

	regmap_update_bits(i2s_tdm->regmap, I2S_XFER, msk, val);
	local_irq_restore(flags);

	dev_dbg(i2s_tdm->dev, "STREAM[%d]: TDM-H: %d, TDM-L: %d, I2S-H: %d, I2S-L: %d\n", stream,
		CLK_MAX_COUNT - tdm_h, CLK_MAX_COUNT - tdm_l,
		CLK_MAX_COUNT - i2s_h, CLK_MAX_COUNT - i2s_l);

	return 0;
}
#endif

static void rockchip_i2s_tdm_xfer_start(struct rk_i2s_tdm_dev *i2s_tdm,
					int stream)
{
#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
	if (i2s_tdm->is_tdm_multi_lanes) {
		if (rockchip_i2s_tdm_multi_lanes_start(i2s_tdm, stream) != -ENOSYS)
			return;
	}
#endif
	if (i2s_tdm->clk_trcm) {
		rockchip_i2s_tdm_reset_assert(i2s_tdm);
		regmap_update_bits(i2s_tdm->regmap, I2S_XFER,
				   I2S_XFER_TXS_MASK |
				   I2S_XFER_RXS_MASK,
				   I2S_XFER_TXS_START |
				   I2S_XFER_RXS_START);
		rockchip_i2s_tdm_reset_deassert(i2s_tdm);
	} else if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		regmap_update_bits(i2s_tdm->regmap, I2S_XFER,
				   I2S_XFER_TXS_MASK,
				   I2S_XFER_TXS_START);
	} else {
		regmap_update_bits(i2s_tdm->regmap, I2S_XFER,
				   I2S_XFER_RXS_MASK,
				   I2S_XFER_RXS_START);
	}
}

static void rockchip_i2s_tdm_xfer_stop(struct rk_i2s_tdm_dev *i2s_tdm,
				       int stream, bool force)
{
	unsigned int msk, val, clr;

	if (i2s_tdm->quirks & QUIRK_ALWAYS_ON && !force)
		return;

	if (i2s_tdm->clk_trcm) {
		msk = I2S_XFER_TXS_MASK | I2S_XFER_RXS_MASK;
		val = I2S_XFER_TXS_STOP | I2S_XFER_RXS_STOP;
		clr = I2S_CLR_TXC | I2S_CLR_RXC;
	} else if (stream == SNDRV_PCM_STREAM_PLAYBACK) {
		msk = I2S_XFER_TXS_MASK;
		val = I2S_XFER_TXS_STOP;
		clr = I2S_CLR_TXC;
	} else {
		msk = I2S_XFER_RXS_MASK;
		val = I2S_XFER_RXS_STOP;
		clr = I2S_CLR_RXC;
	}

	regmap_update_bits(i2s_tdm->regmap, I2S_XFER, msk, val);

	/* delay for LRCK signal integrity */
	udelay(150);

	rockchip_i2s_tdm_clear(i2s_tdm, clr);
}

static void rockchip_i2s_tdm_xfer_trcm_start(struct rk_i2s_tdm_dev *i2s_tdm)
{
	unsigned long flags;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (atomic_inc_return(&i2s_tdm->refcount) == 1)
		rockchip_i2s_tdm_xfer_start(i2s_tdm, 0);
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
}

static void rockchip_i2s_tdm_xfer_trcm_stop(struct rk_i2s_tdm_dev *i2s_tdm)
{
	unsigned long flags;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (atomic_dec_and_test(&i2s_tdm->refcount))
		rockchip_i2s_tdm_xfer_stop(i2s_tdm, 0, false);
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
}

static void rockchip_i2s_tdm_trcm_pause(struct snd_pcm_substream *substream,
					struct rk_i2s_tdm_dev *i2s_tdm)
{
	int stream = substream->stream;
	int bstream = SNDRV_PCM_STREAM_LAST - stream;

	/* store the current state, prepare for resume if necessary */
	i2s_tdm->is_dma_active[bstream] = is_dma_active(i2s_tdm, bstream);

	/* disable dma for both tx and rx */
	rockchip_i2s_tdm_dma_ctrl(i2s_tdm, stream, 0);
	rockchip_i2s_tdm_dma_ctrl(i2s_tdm, bstream, 0);
	rockchip_i2s_tdm_xfer_stop(i2s_tdm, bstream, true);
}

static void rockchip_i2s_tdm_trcm_resume(struct snd_pcm_substream *substream,
					 struct rk_i2s_tdm_dev *i2s_tdm)
{
	int bstream = SNDRV_PCM_STREAM_LAST - substream->stream;

	/*
	 * just resume bstream, because current stream will be
	 * startup in the trigger-cmd-START
	 */
	if (i2s_tdm->is_dma_active[bstream])
		rockchip_i2s_tdm_dma_ctrl(i2s_tdm, bstream, 1);
	rockchip_i2s_tdm_xfer_start(i2s_tdm, bstream);
}

static void rockchip_i2s_tdm_start(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	/*
	 * On HDMI-PATH-ALWAYS-ON situation, we almost keep XFER always on,
	 * so, for new data start, suggested to STOP-CLEAR-START to make sure
	 * data aligned.
	 */
	if ((i2s_tdm->quirks & QUIRK_HDMI_PATH) &&
	    (i2s_tdm->quirks & QUIRK_ALWAYS_ON) &&
	    (stream == SNDRV_PCM_STREAM_PLAYBACK)) {
		rockchip_i2s_tdm_xfer_stop(i2s_tdm, stream, true);
	}

	rockchip_i2s_tdm_dma_ctrl(i2s_tdm, stream, 1);

	if (i2s_tdm->clk_trcm)
		rockchip_i2s_tdm_xfer_trcm_start(i2s_tdm);
	else
		rockchip_i2s_tdm_xfer_start(i2s_tdm, stream);
}

static void rockchip_i2s_tdm_stop(struct rk_i2s_tdm_dev *i2s_tdm, int stream)
{
	rockchip_i2s_tdm_dma_ctrl(i2s_tdm, stream, 0);

	if (i2s_tdm->clk_trcm)
		rockchip_i2s_tdm_xfer_trcm_stop(i2s_tdm);
	else
		rockchip_i2s_tdm_xfer_stop(i2s_tdm, stream, false);
}


static int i2s_checked_set_fmt(struct rk_i2s_tdm_dev *i2s_tdm,
			       struct device *dev, unsigned int fmt)
{
	unsigned long flags;
	int ret, put_ret;

	/* This checked board path is the locked ordinary I2S master profile. */
	if (fmt != (SND_SOC_DAIFMT_CBS_CFS | SND_SOC_DAIFMT_NB_NF |
		    SND_SOC_DAIFMT_I2S))
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	ret = i2s_checked_gate_locked(i2s_tdm);
	if (!ret)
		i2s_tdm->configuring = true;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (ret)
		return ret;

	ret = pm_runtime_get_sync(dev);
	if (ret < 0) {
		pm_runtime_put_noidle(dev);
		goto out;
	}
	ret = regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				 I2S_CKR_MSS_MASK, I2S_CKR_MSS_MASTER);
	if (ret)
		goto put;
	ret = regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				 I2S_CKR_CKP_MASK | I2S_CKR_TLP_MASK |
				 I2S_CKR_RLP_MASK, I2S_CKR_CKP_NORMAL |
				 I2S_CKR_TLP_NORMAL | I2S_CKR_RLP_NORMAL);
	if (ret)
		goto put;
	ret = regmap_update_bits(i2s_tdm->regmap, I2S_TXCR,
				 I2S_TXCR_IBM_MASK | I2S_TXCR_TFS_MASK |
				 I2S_TXCR_PBM_MASK, I2S_TXCR_IBM_NORMAL);
	if (ret)
		goto put;
	ret = regmap_update_bits(i2s_tdm->regmap, I2S_RXCR,
				 I2S_RXCR_IBM_MASK | I2S_RXCR_TFS_MASK |
				 I2S_RXCR_PBM_MASK, I2S_RXCR_IBM_NORMAL);
	if (!ret)
		i2s_tdm->is_master_mode = true;
put:
	/* All format MMIO is finished. Publish any first error before PM.
	 * Keep configuring set: other commands and destruction still reject
	 * this in-flight function, while a successful terminal handoff lets
	 * the asynchronous suspend drain IRQs and release our MCLK refs.
	 */
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (!ret && i2s_tdm->runtime_error)
		ret = i2s_tdm->runtime_error;
	i2s_checked_error_locked(i2s_tdm, ret);
	i2s_tdm->format_pm_release = !ret;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	put_ret = pm_runtime_put(dev);
	if (!ret && put_ret < 0)
		ret = put_ret;
out:
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (!ret && i2s_tdm->runtime_error)
		ret = i2s_tdm->runtime_error;
	i2s_checked_error_locked(i2s_tdm, ret);
	i2s_tdm->format_pm_release = false;
	i2s_tdm->configuring = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int rockchip_i2s_tdm_set_fmt(struct snd_soc_dai *cpu_dai,
				    unsigned int fmt)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(cpu_dai);
	unsigned int mask = 0, val = 0, tdm_val = 0;
	int ret = 0;
	bool is_tdm = i2s_tdm->tdm_mode;

	if (i2s_tdm->checked_lifecycle)
		return i2s_checked_set_fmt(i2s_tdm, cpu_dai->dev, fmt);

	pm_runtime_get_sync(cpu_dai->dev);
	mask = I2S_CKR_MSS_MASK;
	switch (fmt & SND_SOC_DAIFMT_MASTER_MASK) {
	case SND_SOC_DAIFMT_CBS_CFS:
		val = I2S_CKR_MSS_MASTER;
		i2s_tdm->is_master_mode = true;
		break;
	case SND_SOC_DAIFMT_CBM_CFM:
		val = I2S_CKR_MSS_SLAVE;
		i2s_tdm->is_master_mode = false;
		/*
		 * TRCM require TX/RX enabled at the same time, or need the one
		 * which provide clk enabled at first for master mode.
		 *
		 * It is quite a different for slave mode which does not have
		 * these restrictions, because the BCLK / LRCK are provided by
		 * external master devices.
		 *
		 * So, we just set the right clk path value on TRCM register on
		 * stage probe and then drop the trcm value to make TX / RX work
		 * independently.
		 */
		i2s_tdm->clk_trcm = 0;
		break;
	default:
		ret = -EINVAL;
		goto err_pm_put;
	}

	regmap_update_bits(i2s_tdm->regmap, I2S_CKR, mask, val);

	mask = I2S_CKR_CKP_MASK | I2S_CKR_TLP_MASK | I2S_CKR_RLP_MASK;
	switch (fmt & SND_SOC_DAIFMT_INV_MASK) {
	case SND_SOC_DAIFMT_NB_NF:
		val = I2S_CKR_CKP_NORMAL |
		      I2S_CKR_TLP_NORMAL |
		      I2S_CKR_RLP_NORMAL;
		break;
	case SND_SOC_DAIFMT_NB_IF:
		val = I2S_CKR_CKP_NORMAL |
		      I2S_CKR_TLP_INVERTED |
		      I2S_CKR_RLP_INVERTED;
		break;
	case SND_SOC_DAIFMT_IB_NF:
		val = I2S_CKR_CKP_INVERTED |
		      I2S_CKR_TLP_NORMAL |
		      I2S_CKR_RLP_NORMAL;
		break;
	case SND_SOC_DAIFMT_IB_IF:
		val = I2S_CKR_CKP_INVERTED |
		      I2S_CKR_TLP_INVERTED |
		      I2S_CKR_RLP_INVERTED;
		break;
	default:
		ret = -EINVAL;
		goto err_pm_put;
	}

	regmap_update_bits(i2s_tdm->regmap, I2S_CKR, mask, val);

	mask = I2S_TXCR_IBM_MASK | I2S_TXCR_TFS_MASK | I2S_TXCR_PBM_MASK;
	switch (fmt & SND_SOC_DAIFMT_FORMAT_MASK) {
	case SND_SOC_DAIFMT_RIGHT_J:
		val = I2S_TXCR_IBM_RSJM;
		break;
	case SND_SOC_DAIFMT_LEFT_J:
		val = I2S_TXCR_IBM_LSJM;
		break;
	case SND_SOC_DAIFMT_I2S:
		val = I2S_TXCR_IBM_NORMAL;
		break;
	case SND_SOC_DAIFMT_DSP_A: /* PCM delay 1 mode */
		val = I2S_TXCR_TFS_PCM | I2S_TXCR_PBM_MODE(1);
		break;
	case SND_SOC_DAIFMT_DSP_B: /* PCM no delay mode */
		val = I2S_TXCR_TFS_PCM;
		break;
	default:
		ret = -EINVAL;
		goto err_pm_put;
	}

	regmap_update_bits(i2s_tdm->regmap, I2S_TXCR, mask, val);

	mask = I2S_RXCR_IBM_MASK | I2S_RXCR_TFS_MASK | I2S_RXCR_PBM_MASK;
	switch (fmt & SND_SOC_DAIFMT_FORMAT_MASK) {
	case SND_SOC_DAIFMT_RIGHT_J:
		val = I2S_RXCR_IBM_RSJM;
		break;
	case SND_SOC_DAIFMT_LEFT_J:
		val = I2S_RXCR_IBM_LSJM;
		break;
	case SND_SOC_DAIFMT_I2S:
		val = I2S_RXCR_IBM_NORMAL;
		break;
	case SND_SOC_DAIFMT_DSP_A: /* PCM delay 1 mode */
		val = I2S_RXCR_TFS_PCM | I2S_RXCR_PBM_MODE(1);
		break;
	case SND_SOC_DAIFMT_DSP_B: /* PCM no delay mode */
		val = I2S_RXCR_TFS_PCM;
		break;
	default:
		ret = -EINVAL;
		goto err_pm_put;
	}

	regmap_update_bits(i2s_tdm->regmap, I2S_RXCR, mask, val);

	if (is_tdm) {
		switch (fmt & SND_SOC_DAIFMT_FORMAT_MASK) {
		case SND_SOC_DAIFMT_RIGHT_J:
			val = I2S_TXCR_TFS_TDM_I2S;
			tdm_val = TDM_SHIFT_CTRL(2);
			break;
		case SND_SOC_DAIFMT_LEFT_J:
			val = I2S_TXCR_TFS_TDM_I2S;
			tdm_val = TDM_SHIFT_CTRL(1);
			break;
		case SND_SOC_DAIFMT_I2S:
			val = I2S_TXCR_TFS_TDM_I2S;
			tdm_val = TDM_SHIFT_CTRL(0);
			break;
		case SND_SOC_DAIFMT_DSP_A:
			val = I2S_TXCR_TFS_TDM_PCM;
			tdm_val = TDM_SHIFT_CTRL(2);
			break;
		case SND_SOC_DAIFMT_DSP_B:
			val = I2S_TXCR_TFS_TDM_PCM;
			tdm_val = TDM_SHIFT_CTRL(4);
			break;
		default:
			ret = -EINVAL;
			goto err_pm_put;
		}

		tdm_val |= TDM_FSYNC_WIDTH_SEL1(1);
		if (i2s_tdm->tdm_fsync_half_frame)
			tdm_val |= TDM_FSYNC_WIDTH_HALF_FRAME;
		else
			tdm_val |= TDM_FSYNC_WIDTH_ONE_FRAME;

		mask = I2S_TXCR_TFS_MASK;
		regmap_update_bits(i2s_tdm->regmap, I2S_TXCR, mask, val);
		regmap_update_bits(i2s_tdm->regmap, I2S_RXCR, mask, val);

		mask = TDM_FSYNC_WIDTH_SEL1_MSK | TDM_FSYNC_WIDTH_SEL0_MSK |
		       TDM_SHIFT_CTRL_MSK;
		regmap_update_bits(i2s_tdm->regmap, I2S_TDM_TXCR,
				   mask, tdm_val);
		regmap_update_bits(i2s_tdm->regmap, I2S_TDM_RXCR,
				   mask, tdm_val);

		if (val == I2S_TXCR_TFS_TDM_I2S && !i2s_tdm->tdm_fsync_half_frame) {
			/* refine frame width for TDM_I2S_ONE_FRAME */
			mask = TDM_FRAME_WIDTH_MSK;
			tdm_val = TDM_FRAME_WIDTH(i2s_tdm->bclk_fs >> 1);
			regmap_update_bits(i2s_tdm->regmap, I2S_TDM_TXCR,
					   mask, tdm_val);
			regmap_update_bits(i2s_tdm->regmap, I2S_TDM_RXCR,
					   mask, tdm_val);
		}
	}

err_pm_put:
	pm_runtime_put(cpu_dai->dev);

	return ret;
}

static int rockchip_i2s_tdm_clk_set_rate(struct rk_i2s_tdm_dev *i2s_tdm,
					 struct clk *clk, unsigned long rate,
					 int ppm)
{
	unsigned long rate_target;
	int delta, ret;

	if (ppm == i2s_tdm->clk_ppm)
		return 0;

	ret = rockchip_pll_clk_compensation(clk, ppm);
	if (ret != -ENOSYS)
		goto out;

	delta = (ppm < 0) ? -1 : 1;
	delta *= (int)div64_u64((uint64_t)rate * (uint64_t)abs(ppm) + 500000, 1000000);

	rate_target = rate + delta;

	if (!rate_target)
		return -EINVAL;

	ret = clk_set_rate(clk, rate_target);
	if (ret)
		return ret;
out:
	if (!ret)
		i2s_tdm->clk_ppm = ppm;

	return ret;
}

static int rockchip_i2s_tdm_calibrate_mclk(struct rk_i2s_tdm_dev *i2s_tdm,
					   struct snd_pcm_substream *substream,
					   unsigned int lrck_freq)
{
	struct clk *mclk_root;
	struct clk *mclk_parent;
	unsigned int mclk_root_freq;
	unsigned int mclk_root_initial_freq;
	unsigned int mclk_parent_freq;
	unsigned int div, delta;
	uint64_t ppm;
	int ret;

	if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK)
		mclk_parent = i2s_tdm->mclk_tx_src;
	else
		mclk_parent = i2s_tdm->mclk_rx_src;

	switch (lrck_freq) {
	case 8000:
	case 16000:
	case 24000:
	case 32000:
	case 48000:
	case 64000:
	case 96000:
	case 192000:
		mclk_root = i2s_tdm->mclk_root0;
		mclk_root_freq = i2s_tdm->mclk_root0_freq;
		mclk_root_initial_freq = i2s_tdm->mclk_root0_initial_freq;
		mclk_parent_freq = DEFAULT_MCLK_FS * 192000;
		break;
	case 11025:
	case 22050:
	case 44100:
	case 88200:
	case 176400:
		mclk_root = i2s_tdm->mclk_root1;
		mclk_root_freq = i2s_tdm->mclk_root1_freq;
		mclk_root_initial_freq = i2s_tdm->mclk_root1_initial_freq;
		mclk_parent_freq = DEFAULT_MCLK_FS * 176400;
		break;
	default:
		dev_err(i2s_tdm->dev, "Invalid LRCK freq: %u Hz\n",
			lrck_freq);
		return -EINVAL;
	}

	ret = clk_set_parent(mclk_parent, mclk_root);
	if (ret)
		goto out;

	ret = rockchip_i2s_tdm_clk_set_rate(i2s_tdm, mclk_root,
					    mclk_root_freq, 0);
	if (ret)
		goto out;

	delta = abs(mclk_root_freq % mclk_parent_freq - mclk_parent_freq);
	ppm = div64_u64((uint64_t)delta * 1000000, (uint64_t)mclk_root_freq);

	if (ppm) {
		div = DIV_ROUND_CLOSEST(mclk_root_initial_freq, mclk_parent_freq);
		if (!div)
			return -EINVAL;

		mclk_root_freq = mclk_parent_freq * round_up(div, 2);

		ret = clk_set_rate(mclk_root, mclk_root_freq);
		if (ret)
			goto out;

		i2s_tdm->mclk_root0_freq = clk_get_rate(i2s_tdm->mclk_root0);
		i2s_tdm->mclk_root1_freq = clk_get_rate(i2s_tdm->mclk_root1);
	}

	ret = clk_set_rate(mclk_parent, mclk_parent_freq);
	if (ret)
		goto out;

out:
	return ret;
}

static int rockchip_i2s_tdm_mclk_reparent(struct rk_i2s_tdm_dev *i2s_tdm)
{
	struct clk *parent;
	int ret = 0;

	/* reparent to the same clk on TRCM mode */
	switch (i2s_tdm->clk_trcm) {
	case I2S_CKR_TRCM_TXONLY:
		parent = clk_get_parent(i2s_tdm->mclk_tx);
		/*
		 * API clk_has_parent is not available yet on GKI, so we
		 * use clk_set_parent directly and ignore the ret value.
		 * if the API has addressed on GKI, should remove it.
		 */
#ifdef CONFIG_NO_GKI
		if (clk_has_parent(i2s_tdm->mclk_rx, parent))
			ret = clk_set_parent(i2s_tdm->mclk_rx, parent);
#else
		clk_set_parent(i2s_tdm->mclk_rx, parent);
#endif
		break;
	case I2S_CKR_TRCM_RXONLY:
		parent = clk_get_parent(i2s_tdm->mclk_rx);
#ifdef CONFIG_NO_GKI
		if (clk_has_parent(i2s_tdm->mclk_tx, parent))
			ret = clk_set_parent(i2s_tdm->mclk_tx, parent);
#else
		clk_set_parent(i2s_tdm->mclk_tx, parent);
#endif
		break;
	}

	return ret;
}

static int rockchip_i2s_tdm_set_mclk(struct rk_i2s_tdm_dev *i2s_tdm,
				     struct snd_pcm_substream *substream,
				     struct clk **mclk)
{
	unsigned int mclk_freq;
	int ret;

	if (i2s_tdm->clk_trcm) {
		if (i2s_tdm->mclk_tx_freq != i2s_tdm->mclk_rx_freq) {
			dev_err(i2s_tdm->dev,
				"clk_trcm, tx: %d and rx: %d should be same\n",
				i2s_tdm->mclk_tx_freq,
				i2s_tdm->mclk_rx_freq);
			ret = -EINVAL;
			goto err;
		}

		ret = clk_set_rate(i2s_tdm->mclk_tx, i2s_tdm->mclk_tx_freq);
		if (ret)
			goto err;

		ret = clk_set_rate(i2s_tdm->mclk_rx, i2s_tdm->mclk_rx_freq);
		if (ret)
			goto err;

		ret = rockchip_i2s_tdm_mclk_reparent(i2s_tdm);
		if (ret)
			goto err;

		/* mclk_rx is also ok. */
		*mclk = i2s_tdm->mclk_tx;
	} else {
		if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK) {
			*mclk = i2s_tdm->mclk_tx;
			mclk_freq = i2s_tdm->mclk_tx_freq;
		} else {
			*mclk = i2s_tdm->mclk_rx;
			mclk_freq = i2s_tdm->mclk_rx_freq;
		}

		ret = clk_set_rate(*mclk, mclk_freq);
		if (ret)
			goto err;
	}

	return 0;

err:
	return ret;
}

static int rockchip_i2s_io_multiplex(struct snd_pcm_substream *substream,
				     struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	int usable_chs = MULTIPLEX_CH_MAX;
	unsigned int val = 0;

	if (!i2s_tdm->io_multiplex)
		return 0;

	if (IS_ERR(i2s_tdm->grf))
		return 0;

	if (substream->stream == SNDRV_PCM_STREAM_CAPTURE) {
		struct snd_pcm_str *playback_str =
			&substream->pcm->streams[SNDRV_PCM_STREAM_PLAYBACK];

		if (playback_str->substream_opened) {
			regmap_read(i2s_tdm->regmap, I2S_TXCR, &val);
			val &= I2S_TXCR_CSR_MASK;
			usable_chs = MULTIPLEX_CH_MAX - to_ch_num(val);
		}

		regmap_read(i2s_tdm->regmap, I2S_RXCR, &val);
		val &= I2S_RXCR_CSR_MASK;

		if (to_ch_num(val) > usable_chs) {
			dev_err(i2s_tdm->dev,
				"Capture chs(%d) > usable chs(%d)\n",
				to_ch_num(val), usable_chs);
			return -EINVAL;
		}

		switch (val) {
		case I2S_CHN_4:
			val = I2S_IO_6CH_OUT_4CH_IN;
			break;
		case I2S_CHN_6:
			val = I2S_IO_4CH_OUT_6CH_IN;
			break;
		case I2S_CHN_8:
			val = I2S_IO_2CH_OUT_8CH_IN;
			break;
		default:
			val = I2S_IO_8CH_OUT_2CH_IN;
			break;
		}
	} else {
		struct snd_pcm_str *capture_str =
			&substream->pcm->streams[SNDRV_PCM_STREAM_CAPTURE];

		if (capture_str->substream_opened) {
			regmap_read(i2s_tdm->regmap, I2S_RXCR, &val);
			val &= I2S_RXCR_CSR_MASK;
			usable_chs = MULTIPLEX_CH_MAX - to_ch_num(val);
		}

		regmap_read(i2s_tdm->regmap, I2S_TXCR, &val);
		val &= I2S_TXCR_CSR_MASK;

		if (to_ch_num(val) > usable_chs) {
			dev_err(i2s_tdm->dev,
				"Playback chs(%d) > usable chs(%d)\n",
				to_ch_num(val), usable_chs);
			return -EINVAL;
		}

		switch (val) {
		case I2S_CHN_4:
			val = I2S_IO_4CH_OUT_6CH_IN;
			break;
		case I2S_CHN_6:
			val = I2S_IO_6CH_OUT_4CH_IN;
			break;
		case I2S_CHN_8:
			val = I2S_IO_8CH_OUT_2CH_IN;
			break;
		default:
			val = I2S_IO_2CH_OUT_8CH_IN;
			break;
		}
	}

	val <<= i2s_tdm->soc_data->grf_shift;
	val |= (I2S_IO_DIRECTION_MASK << i2s_tdm->soc_data->grf_shift) << 16;
	regmap_write(i2s_tdm->grf, i2s_tdm->soc_data->grf_reg_offset, val);

	return 0;
}

static bool is_params_dirty(struct snd_pcm_substream *substream,
			    struct snd_soc_dai *dai,
			    unsigned int div_bclk,
			    unsigned int div_lrck,
			    unsigned int fmt)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned int last_div_bclk, last_div_lrck, last_fmt, val;

	regmap_read(i2s_tdm->regmap, I2S_CLKDIV, &val);
	last_div_bclk = ((val & I2S_CLKDIV_TXM_MASK) >> I2S_CLKDIV_TXM_SHIFT) + 1;
	if (last_div_bclk != div_bclk)
		return true;

	regmap_read(i2s_tdm->regmap, I2S_CKR, &val);
	last_div_lrck = ((val & I2S_CKR_TSD_MASK) >> I2S_CKR_TSD_SHIFT) + 1;
	if (last_div_lrck != div_lrck)
		return true;

	if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK) {
		regmap_read(i2s_tdm->regmap, I2S_TXCR, &val);
		last_fmt = val & (I2S_TXCR_VDW_MASK | I2S_TXCR_CSR_MASK);
	} else {
		regmap_read(i2s_tdm->regmap, I2S_RXCR, &val);
		last_fmt = val & (I2S_RXCR_VDW_MASK | I2S_RXCR_CSR_MASK);
	}
	if (last_fmt != fmt)
		return true;

	return false;
}

static int rockchip_i2s_tdm_params_trcm(struct snd_pcm_substream *substream,
					struct snd_soc_dai *dai,
					unsigned int div_bclk,
					unsigned int div_lrck,
					unsigned int fmt)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned long flags;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (atomic_read(&i2s_tdm->refcount))
		rockchip_i2s_tdm_trcm_pause(substream, i2s_tdm);

	regmap_update_bits(i2s_tdm->regmap, I2S_CLKDIV,
			   I2S_CLKDIV_TXM_MASK | I2S_CLKDIV_RXM_MASK,
			   I2S_CLKDIV_TXM(div_bclk) | I2S_CLKDIV_RXM(div_bclk));
	regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
			   I2S_CKR_TSD_MASK | I2S_CKR_RSD_MASK,
			   I2S_CKR_TSD(div_lrck) | I2S_CKR_RSD(div_lrck));

	if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK)
		regmap_update_bits(i2s_tdm->regmap, I2S_TXCR,
				   I2S_TXCR_VDW_MASK | I2S_TXCR_CSR_MASK,
				   fmt);
	else
		regmap_update_bits(i2s_tdm->regmap, I2S_RXCR,
				   I2S_RXCR_VDW_MASK | I2S_RXCR_CSR_MASK,
				   fmt);

	if (atomic_read(&i2s_tdm->refcount))
		rockchip_i2s_tdm_trcm_resume(substream, i2s_tdm);
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);

	return 0;
}

static int rockchip_i2s_tdm_params(struct snd_pcm_substream *substream,
				   struct snd_soc_dai *dai,
				   unsigned int div_bclk,
				   unsigned int div_lrck,
				   unsigned int fmt)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	int stream = substream->stream;

	if (is_stream_active(i2s_tdm, stream))
		rockchip_i2s_tdm_xfer_stop(i2s_tdm, stream, true);

	if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK) {
		regmap_update_bits(i2s_tdm->regmap, I2S_CLKDIV,
				   I2S_CLKDIV_TXM_MASK,
				   I2S_CLKDIV_TXM(div_bclk));
		regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				   I2S_CKR_TSD_MASK,
				   I2S_CKR_TSD(div_lrck));
		regmap_update_bits(i2s_tdm->regmap, I2S_TXCR,
				   I2S_TXCR_VDW_MASK | I2S_TXCR_CSR_MASK,
				   fmt);
	} else {
		regmap_update_bits(i2s_tdm->regmap, I2S_CLKDIV,
				   I2S_CLKDIV_RXM_MASK,
				   I2S_CLKDIV_RXM(div_bclk));
		regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				   I2S_CKR_RSD_MASK,
				   I2S_CKR_RSD(div_lrck));
		regmap_update_bits(i2s_tdm->regmap, I2S_RXCR,
				   I2S_RXCR_VDW_MASK | I2S_RXCR_CSR_MASK,
				   fmt);
	}

	/*
	 * Bring back CLK ASAP after cfg changed to make SINK devices active
	 * on HDMI-PATH-ALWAYS-ON situation, this workaround for some TVs no
	 * sound issue. at the moment, it's 8K@60Hz display situation.
	 */
	if ((i2s_tdm->quirks & QUIRK_HDMI_PATH) &&
	    (i2s_tdm->quirks & QUIRK_ALWAYS_ON) &&
	    (substream->stream == SNDRV_PCM_STREAM_PLAYBACK)) {
		rockchip_i2s_tdm_xfer_start(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK);
	}

	return 0;
}

static int rockchip_i2s_tdm_params_channels(struct snd_pcm_substream *substream,
					    struct snd_pcm_hw_params *params,
					    struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned int reg_fmt, fmt;
	int ret = 0;

#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
	if (i2s_tdm->is_tdm_multi_lanes) {
		unsigned int lanes = rockchip_i2s_tdm_get_lanes(i2s_tdm,
								substream->stream);

		switch (lanes) {
		case 4:
			ret = I2S_CHN_8;
			break;
		case 3:
			ret = I2S_CHN_6;
			break;
		case 2:
			ret = I2S_CHN_4;
			break;
		case 1:
			ret = I2S_CHN_2;
			break;
		default:
			ret = -EINVAL;
			break;
		}

		return ret;
	}
#endif
	if (substream->stream == SNDRV_PCM_STREAM_PLAYBACK)
		reg_fmt = I2S_TXCR;
	else
		reg_fmt = I2S_RXCR;

	ret = regmap_read(i2s_tdm->regmap, reg_fmt, &fmt);
	if (i2s_tdm->checked_lifecycle && ret < 0)
		return ret;
	fmt &= I2S_TXCR_TFS_MASK;

	if (fmt == I2S_TXCR_TFS_TDM_I2S && !i2s_tdm->tdm_fsync_half_frame) {
		switch (params_channels(params)) {
		case 16:
			ret = I2S_CHN_8;
			break;
		case 12:
			ret = I2S_CHN_6;
			break;
		case 8:
			ret = I2S_CHN_4;
			break;
		case 4:
			ret = I2S_CHN_2;
			break;
		default:
			ret = -EINVAL;
			break;
		}
	} else {
		switch (params_channels(params)) {
		case 8:
			ret = I2S_CHN_8;
			break;
		case 6:
			ret = I2S_CHN_6;
			break;
		case 4:
			ret = I2S_CHN_4;
			break;
		case 2:
			ret = I2S_CHN_2;
			break;
		default:
			ret = -EINVAL;
			break;
		}
	}

	return ret;
}


static int i2s_checked_params_dirty(struct snd_pcm_substream *substream,
				  struct rk_i2s_tdm_dev *i2s_tdm,
				  unsigned int div_bclk,
				  unsigned int div_lrck,
				  unsigned int fmt, bool *dirty)
{
	unsigned int val;
	int ret;

	*dirty = false;
	ret = regmap_read(i2s_tdm->regmap, I2S_CLKDIV, &val);
	if (ret)
		return ret;
	*dirty |= (((val & I2S_CLKDIV_TXM_MASK) >> I2S_CLKDIV_TXM_SHIFT) + 1 != div_bclk);
	ret = regmap_read(i2s_tdm->regmap, I2S_CKR, &val);
	if (ret)
		return ret;
	*dirty |= (((val & I2S_CKR_TSD_MASK) >> I2S_CKR_TSD_SHIFT) + 1 != div_lrck);
	ret = regmap_read(i2s_tdm->regmap,
			  substream->stream == SNDRV_PCM_STREAM_PLAYBACK ?
			  I2S_TXCR : I2S_RXCR, &val);
	if (ret)
		return ret;
	*dirty |= ((val & (I2S_TXCR_VDW_MASK | I2S_TXCR_CSR_MASK)) != fmt);
	return 0;
}

static int i2s_checked_params_trcm(struct snd_pcm_substream *substream,
				 struct rk_i2s_tdm_dev *i2s_tdm,
				 unsigned int div_bclk,
				 unsigned int div_lrck, unsigned int fmt)
{
	int ret;

	ret = regmap_update_bits(i2s_tdm->regmap, I2S_CLKDIV,
				 I2S_CLKDIV_TXM_MASK | I2S_CLKDIV_RXM_MASK,
				 I2S_CLKDIV_TXM(div_bclk) | I2S_CLKDIV_RXM(div_bclk));
	if (ret)
		return ret;
	ret = regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				 I2S_CKR_TSD_MASK | I2S_CKR_RSD_MASK,
				 I2S_CKR_TSD(div_lrck) | I2S_CKR_RSD(div_lrck));
	if (ret)
		return ret;
	return regmap_update_bits(i2s_tdm->regmap,
				  substream->stream == SNDRV_PCM_STREAM_PLAYBACK ?
				  I2S_TXCR : I2S_RXCR,
				  I2S_TXCR_VDW_MASK | I2S_TXCR_CSR_MASK, fmt);
}

static int i2s_shared_begin(struct snd_pcm_substream *ss,
		struct snd_pcm_hw_params *params, struct snd_soc_dai *dai, u64 *cookie)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_soc_dai_params_state *p = &i2s_tdm->shared_params;
	unsigned long flags;
	int ret;

	/* Pure finite profile rejection precedes even endpoint admission. */
	if (!i2s_tdm->shared_params_enabled || !i2s_tdm->checked_lifecycle ||
	    (ss->stream != 0 && ss->stream != 1) || params_rate(params) != 48000 ||
	    params_channels(params) != 2 || params_format(params) != SNDRV_PCM_FORMAT_S16_LE ||
	    !i2s_tdm->is_master_mode || i2s_tdm->clk_trcm != I2S_CKR_TRCM_TXONLY ||
	    i2s_tdm->bclk_fs != 64 || i2s_tdm->lrck_ratio != 1 || i2s_tdm->tdm_mode ||
	    i2s_tdm->mclk_calibrate || i2s_tdm->io_multiplex)
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	ret = i2s_checked_gate_locked(i2s_tdm);
	if (!ret && (i2s_tdm->shared_dai_fmt != (SND_SOC_DAIFMT_CBS_CFS |
		SND_SOC_DAIFMT_NB_NF | SND_SOC_DAIFMT_I2S) ||
		!i2s_tdm->ready || i2s_tdm->substreams[ss->stream] != ss))
		ret = -EINVAL;
	if (!ret)
		ret = snd_soc_dai_params_begin_locked(p, dai, ss, cookie);
	if (!ret) {
		p->old_clock[0] = i2s_tdm->mclk_tx_freq;
		p->old_clock[1] = i2s_tdm->mclk_rx_freq;
		i2s_tdm->configuring = true;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_shared_reuse(struct snd_pcm_substream *ss, struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned long flags;
	int ret;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	ret = i2s_tdm->shared_params.pending == ss ? i2s_tdm->shared_params.reuse : -EINVAL;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static void i2s_shared_abort(struct snd_pcm_substream *ss, struct snd_soc_dai *dai,
			     u64 cookie, int first_error, bool shared_io_possible)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_soc_dai_params_state *p = &i2s_tdm->shared_params;
	unsigned long flags;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (snd_soc_dai_params_abort_locked(p, dai, ss, cookie)) {
		i2s_tdm->mclk_tx_freq = p->old_clock[0];
		i2s_tdm->mclk_rx_freq = p->old_clock[1];
		i2s_tdm->shared_prepared &= ~BIT(ss->stream);
		if (shared_io_possible)
			i2s_checked_error_locked(i2s_tdm, first_error < 0 ? first_error : -EIO);
		i2s_tdm->configuring = false;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
}

static int i2s_shared_commit(struct snd_pcm_substream *ss,
			      struct snd_soc_dai *dai, u64 cookie)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_soc_dai_params_state *p = &i2s_tdm->shared_params;
	unsigned long flags;
	int ret;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (!cookie || p->cookie != cookie || p->pending != ss ||
	    p->transaction != ss || !p->undo)
		ret = -EINVAL;
	else if (i2s_tdm->runtime_error)
		ret = i2s_tdm->runtime_error;
	else if (i2s_tdm->shutting_down)
		ret = -ESHUTDOWN;
	else if (i2s_tdm->power_transition || i2s_tdm->started || !i2s_tdm->configuring)
		ret = -EBUSY;
	else
		ret = snd_soc_dai_params_commit_locked(p, dai, ss, cookie);
	if (!ret) {
		i2s_tdm->mclk_tx_freq = 12288000;
		i2s_tdm->mclk_rx_freq = 12288000;
		i2s_tdm->shared_prepared &= ~BIT(ss->stream);
		i2s_tdm->configuring = false;
	}
	/* Any matched failure keeps the whole-chain gate until terminal CPU abort.
	 * A duplicate/stale commit has no pending identity and changes no state.
	 */
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static void i2s_shared_fault(struct snd_soc_dai *dai, int first_errno)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned long flags;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	i2s_checked_error_locked(i2s_tdm, first_errno < 0 ? first_errno : -EIO);
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
}

static int i2s_shared_free_check(struct snd_pcm_substream *ss, struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_soc_dai_params_state *p = &i2s_tdm->shared_params;
	unsigned long flags;
	int ret = 0;

	if (ss->stream != 0 && ss->stream != 1)
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (i2s_tdm->configuring || p->pending || ((i2s_tdm->started | i2s_tdm->starting) & BIT(ss->stream)))
		ret = -EBUSY;
	else if ((p->owner[ss->stream] && p->owner[ss->stream] != ss) ||
		 i2s_tdm->substreams[ss->stream] != ss)
		ret = -EINVAL;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_shared_free(struct snd_pcm_substream *ss, struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned long flags;
	int ret;

	if (ss->stream != 0 && ss->stream != 1)
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (i2s_tdm->configuring || ((i2s_tdm->started | i2s_tdm->starting) & BIT(ss->stream)))
		ret = -EBUSY;
	else
		ret = snd_soc_dai_params_free_locked(&i2s_tdm->shared_params, dai, ss);
	if (!ret)
		i2s_tdm->shared_prepared &= ~BIT(ss->stream);
	/* Faults, physical clocks and open leases are never reset by params free. */
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_shared_sysclk(struct snd_soc_dai *dai, int clk_id,
			      unsigned int freq, int dir)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_soc_dai_params_state *p = &i2s_tdm->shared_params;
	unsigned long flags;
	int ret;

	if (clk_id != 0 || (dir != SND_SOC_CLOCK_IN && dir != SND_SOC_CLOCK_OUT) ||
	    (!freq && dir != SND_SOC_CLOCK_OUT))
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (i2s_tdm->runtime_error)
		ret = i2s_tdm->runtime_error;
	else if (i2s_tdm->shutting_down)
		ret = -ESHUTDOWN;
	else if (p->pending || p->owner[0] || p->owner[1])
		/* This validates a value; it does not authenticate the caller/owner. */
		ret = freq == 12288000 ? 0 : -EINVAL;
	else
		ret = freq ? (freq == 12288000 ? 0 : -EINVAL) : i2s_checked_gate_locked(i2s_tdm);
	if (!ret && !freq && (!i2s_tdm->stop_proven || !i2s_tdm->irq_drained ||
			      i2s_tdm->substreams[0] || i2s_tdm->substreams[1]))
		ret = -EBUSY;
	if (!ret && !freq) {
		i2s_tdm->mclk_tx_freq = 0;
		i2s_tdm->mclk_rx_freq = 0;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_shared_hw_params(struct snd_pcm_substream *ss,
		struct snd_pcm_hw_params *params, struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_soc_dai_params_state *p = &i2s_tdm->shared_params;
	struct snd_dmaengine_dai_dma_data *dma_data;
	unsigned long flags;
	unsigned int fmt;
	bool reuse, dirty;
	int ret;

	if ((ss->stream != 0 && ss->stream != 1) || params_rate(params) != 48000 ||
	    params_channels(params) != 2 || params_format(params) != SNDRV_PCM_FORMAT_S16_LE)
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	ret = i2s_tdm->runtime_error;
	if (!ret && (p->pending != ss || !p->undo || p->applied ||
		     !i2s_tdm->configuring || i2s_tdm->power_transition ||
		     i2s_tdm->shutting_down || i2s_tdm->started))
		ret = -EBUSY;
	if (!ret)
		p->applied = true;
	reuse = p->reuse;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (ret)
		return ret;
	if (!reuse) {
		/* The pending fixed clock is consumed by this explicit substream. */
		ret = clk_set_rate(i2s_tdm->mclk_tx, 12288000);
		if (!ret)
			ret = clk_set_rate(i2s_tdm->mclk_rx, 12288000);
		if (!ret)
			ret = rockchip_i2s_tdm_mclk_reparent(i2s_tdm);
		if (!ret && (clk_get_rate(i2s_tdm->mclk_tx) != 12288000 ||
			     clk_get_rate(i2s_tdm->mclk_rx) != 12288000))
			ret = -EINVAL;
		if (ret)
			return ret;
		ret = rockchip_i2s_tdm_params_channels(ss, params, dai);
		if (ret < 0)
			return ret;
		fmt = I2S_TXCR_VDW(16) | ret;
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		ret = i2s_tdm->runtime_error;
		if (!ret && (i2s_tdm->shutting_down || i2s_tdm->power_transition))
			ret = -ESHUTDOWN;
		if (!ret)
			ret = i2s_checked_params_dirty(ss, i2s_tdm, 4, 64, fmt, &dirty);
		if (!ret && dirty)
			ret = i2s_checked_params_trcm(ss, i2s_tdm, 4, 64, fmt);
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		if (ret)
			return ret;
	}
	/* Reuse touches only the new direction's RAM DMA configuration. */
	dma_data = snd_soc_dai_get_dma_data(dai, ss);
	dma_data->maxburst = MAXBURST_PER_FIFO;
	return 0;
}

static int i2s_shared_set_fmt(struct snd_soc_dai *dai, unsigned int fmt)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned long flags;
	int ret;

	if (fmt != (SND_SOC_DAIFMT_CBS_CFS | SND_SOC_DAIFMT_NB_NF | SND_SOC_DAIFMT_I2S))
		return -EINVAL;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (i2s_tdm->starting) {
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		return -EBUSY;
	}
	if (i2s_tdm->shared_params.pending || i2s_tdm->shared_params.owner[0] ||
	    i2s_tdm->shared_params.owner[1]) {
		ret = i2s_tdm->shared_dai_fmt == fmt ? 0 : -EBUSY;
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		return ret;
	}
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	ret = i2s_checked_set_fmt(i2s_tdm, dai->dev, fmt);
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (!ret)
		i2s_tdm->shared_dai_fmt = fmt;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_shared_tdm_slot(struct snd_soc_dai *dai, unsigned int tx_mask,
		unsigned int rx_mask, int slots, int slot_width)
{
	/* No TDM conversion belongs to the finite ordinary-I2S profile. */
	return -EINVAL;
}

static int i2s_checked_hw_params(struct snd_pcm_substream *substream,
			       struct snd_pcm_hw_params *params,
			       struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_dmaengine_dai_dma_data *dma_data;
	struct clk *mclk;
	unsigned int fmt, div_bclk, div_lrck;
	unsigned int channels = params_channels(params);
	u64 lrck_rate, bclk_rate;
	unsigned long flags;
	bool dirty;
	int ret;

	if (substream->stream != SNDRV_PCM_STREAM_PLAYBACK &&
	    substream->stream != SNDRV_PCM_STREAM_CAPTURE)
		return -EINVAL;
	if (!params_rate(params) || !i2s_tdm->lrck_ratio ||
	    (channels != 2 && channels != 4 && channels != 6 && channels != 8))
		return -EINVAL;
	switch (params_format(params)) {
	case SNDRV_PCM_FORMAT_S8:
		fmt = I2S_TXCR_VDW(8);
		break;
	case SNDRV_PCM_FORMAT_S16_LE:
		fmt = I2S_TXCR_VDW(16);
		break;
	case SNDRV_PCM_FORMAT_S20_3LE:
		fmt = I2S_TXCR_VDW(20);
		break;
	case SNDRV_PCM_FORMAT_S24_LE:
		fmt = I2S_TXCR_VDW(24);
		break;
	case SNDRV_PCM_FORMAT_S32_LE:
	case SNDRV_PCM_FORMAT_IEC958_SUBFRAME_LE:
		fmt = I2S_TXCR_VDW(32);
		break;
	default:
		return -EINVAL;
	}
	lrck_rate = (u64)params_rate(params) * i2s_tdm->lrck_ratio;
	bclk_rate = lrck_rate * i2s_tdm->bclk_fs;
	if (!bclk_rate || bclk_rate > 0xffffffffULL || lrck_rate > 0xffffffffULL)
		return -EINVAL;

	/* Admission spans sleeping clock operations without holding spinlock. */
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	ret = i2s_checked_gate_locked(i2s_tdm);
	/* A shutdown cache reset requires a new positive sysclk request first. */
	if (!ret && (!i2s_tdm->mclk_tx_freq || !i2s_tdm->mclk_rx_freq))
		ret = -EINVAL;
	if (!ret)
		i2s_tdm->configuring = true;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	if (ret)
		return ret;
	ret = rockchip_i2s_tdm_set_mclk(i2s_tdm, substream, &mclk);
	if (ret)
		goto out;
	div_bclk = DIV_ROUND_CLOSEST(clk_get_rate(mclk), (unsigned int)bclk_rate);
	div_lrck = (unsigned int)(bclk_rate / lrck_rate);
	if (!div_bclk || div_bclk > 256 || !div_lrck || div_lrck > 256) {
		ret = -EINVAL;
		goto out;
	}
	ret = rockchip_i2s_tdm_params_channels(substream, params, dai);
	if (ret < 0)
		goto out;
	fmt |= ret;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (i2s_tdm->runtime_error)
		ret = i2s_tdm->runtime_error;
	else if (i2s_tdm->shutting_down)
		ret = -ESHUTDOWN;
	else
		ret = i2s_checked_params_dirty(substream, i2s_tdm, div_bclk, div_lrck, fmt, &dirty);
	if (!ret && dirty)
		ret = i2s_checked_params_trcm(substream, i2s_tdm, div_bclk, div_lrck, fmt);
	if (!ret) {
		dma_data = snd_soc_dai_get_dma_data(dai, substream);
		dma_data->maxburst = MAXBURST_PER_FIFO * channels / 2;
	}
	i2s_checked_error_locked(i2s_tdm, ret);
	i2s_tdm->configuring = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
out:
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	i2s_checked_error_locked(i2s_tdm, ret);
	i2s_tdm->configuring = false;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int rockchip_i2s_tdm_hw_params(struct snd_pcm_substream *substream,
				      struct snd_pcm_hw_params *params,
				      struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	struct snd_dmaengine_dai_dma_data *dma_data;
	struct clk *mclk;
	int ret = 0;
	unsigned int val = 0;
	unsigned int mclk_rate, bclk_rate, lrck_rate, div_bclk = 4, div_lrck = 64;

	if (i2s_tdm->checked_lifecycle)
		return i2s_checked_hw_params(substream, params, dai);

#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
	if (i2s_tdm->is_tdm_multi_lanes)
		rockchip_i2s_tdm_multi_lanes_set_clk(substream, params, dai);
#endif
	dma_data = snd_soc_dai_get_dma_data(dai, substream);
	dma_data->maxburst = MAXBURST_PER_FIFO * params_channels(params) / 2;

	if (i2s_tdm->mclk_calibrate)
		rockchip_i2s_tdm_calibrate_mclk(i2s_tdm, substream,
						params_rate(params));

	ret = rockchip_i2s_tdm_set_mclk(i2s_tdm, substream, &mclk);
	if (ret)
		goto err;

	mclk_rate = clk_get_rate(mclk);
	lrck_rate = params_rate(params) * i2s_tdm->lrck_ratio;
	bclk_rate = i2s_tdm->bclk_fs * lrck_rate;
	if (!bclk_rate) {
		ret = -EINVAL;
		goto err;
	}
	div_bclk = DIV_ROUND_CLOSEST(mclk_rate, bclk_rate);
	div_lrck = bclk_rate / lrck_rate;

	switch (params_format(params)) {
	case SNDRV_PCM_FORMAT_S8:
		val |= I2S_TXCR_VDW(8);
		break;
	case SNDRV_PCM_FORMAT_S16_LE:
		val |= I2S_TXCR_VDW(16);
		break;
	case SNDRV_PCM_FORMAT_S20_3LE:
		val |= I2S_TXCR_VDW(20);
		break;
	case SNDRV_PCM_FORMAT_S24_LE:
		val |= I2S_TXCR_VDW(24);
		break;
	case SNDRV_PCM_FORMAT_S32_LE:
	case SNDRV_PCM_FORMAT_IEC958_SUBFRAME_LE:
		val |= I2S_TXCR_VDW(32);
		break;
	default:
		ret = -EINVAL;
		goto err;
	}

	ret = rockchip_i2s_tdm_params_channels(substream, params, dai);
	if (ret < 0)
		goto err;

	val |= ret;
	if (!is_params_dirty(substream, dai, div_bclk, div_lrck, val))
		return 0;

	if (i2s_tdm->clk_trcm)
		rockchip_i2s_tdm_params_trcm(substream, dai, div_bclk, div_lrck, val);
	else
		rockchip_i2s_tdm_params(substream, dai, div_bclk, div_lrck, val);

	ret = rockchip_i2s_io_multiplex(substream, dai);

err:
	return ret;
}

static int rockchip_i2s_tdm_trigger(struct snd_pcm_substream *substream,
				    int cmd, struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	int ret = 0;

	if (i2s_tdm->checked_lifecycle) {
		if (i2s_tdm->shared_params_enabled)
			return i2s_trcm_trigger(i2s_tdm, substream, cmd);
		return i2s_checked_trigger(i2s_tdm, substream->stream, cmd);
	}

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		rockchip_i2s_tdm_start(i2s_tdm, substream->stream);
		break;
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		rockchip_i2s_tdm_stop(i2s_tdm, substream->stream);
		break;
	default:
		ret = -EINVAL;
		break;
	}

	return ret;
}

static int rockchip_i2s_tdm_set_sysclk(struct snd_soc_dai *cpu_dai, int stream,
				       unsigned int freq, int dir)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(cpu_dai);

	unsigned long flags;
	int ret;

	if (i2s_tdm->checked_lifecycle) {
		if (stream != SNDRV_PCM_STREAM_PLAYBACK && stream != SNDRV_PCM_STREAM_CAPTURE)
			return -EINVAL;
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		ret = i2s_checked_gate_locked(i2s_tdm);
		/* Machine shutdown resets cached requests after DAI STOP/IRQ drain. */
		if (!ret && !freq &&
		    (!i2s_tdm->stop_proven || !i2s_tdm->irq_drained ||
		     READ_ONCE(i2s_tdm->substreams[0]) || READ_ONCE(i2s_tdm->substreams[1])))
			ret = -EBUSY;
		/* Zero only clears the shared TRCM request; never clk_set_rate(0). */
		if (!ret) {
			i2s_tdm->mclk_tx_freq = freq;
			i2s_tdm->mclk_rx_freq = freq;
		}
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		return ret;
	}

	/* Put set mclk rate into rockchip_i2s_tdm_set_mclk() */
	if (i2s_tdm->clk_trcm) {
		i2s_tdm->mclk_tx_freq = freq;
		i2s_tdm->mclk_rx_freq = freq;
	} else {
		if (stream == SNDRV_PCM_STREAM_PLAYBACK)
			i2s_tdm->mclk_tx_freq = freq;
		else
			i2s_tdm->mclk_rx_freq = freq;
	}

	dev_dbg(i2s_tdm->dev, "The target mclk_%s freq is: %d\n",
		stream ? "rx" : "tx", freq);

	return 0;
}

static int rockchip_i2s_tdm_clk_compensation_info(struct snd_kcontrol *kcontrol,
						  struct snd_ctl_elem_info *uinfo)
{
	uinfo->type = SNDRV_CTL_ELEM_TYPE_INTEGER;
	uinfo->count = 1;
	uinfo->value.integer.min = CLK_PPM_MIN;
	uinfo->value.integer.max = CLK_PPM_MAX;
	uinfo->value.integer.step = 1;

	return 0;
}

static int rockchip_i2s_tdm_clk_compensation_get(struct snd_kcontrol *kcontrol,
						 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_dai *dai = snd_kcontrol_chip(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_dai_get_drvdata(dai);

	ucontrol->value.integer.value[0] = i2s_tdm->clk_ppm;

	return 0;
}

static int rockchip_i2s_tdm_clk_compensation_put(struct snd_kcontrol *kcontrol,
						 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_dai *dai = snd_kcontrol_chip(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_dai_get_drvdata(dai);
	int ret = 0, ppm = 0;

	if ((ucontrol->value.integer.value[0] < CLK_PPM_MIN) ||
	    (ucontrol->value.integer.value[0] > CLK_PPM_MAX))
		return -EINVAL;

	ppm = ucontrol->value.integer.value[0];

	ret = rockchip_i2s_tdm_clk_set_rate(i2s_tdm, i2s_tdm->mclk_root0,
					    i2s_tdm->mclk_root0_freq, ppm);
	if (ret)
		return ret;

	if (clk_is_match(i2s_tdm->mclk_root0, i2s_tdm->mclk_root1))
		return 0;

	ret = rockchip_i2s_tdm_clk_set_rate(i2s_tdm, i2s_tdm->mclk_root1,
					    i2s_tdm->mclk_root1_freq, ppm);

	return ret;
}

static struct snd_kcontrol_new rockchip_i2s_tdm_compensation_control = {
	.iface = SNDRV_CTL_ELEM_IFACE_PCM,
	.name = "PCM Clk Compensation In PPM",
	.info = rockchip_i2s_tdm_clk_compensation_info,
	.get = rockchip_i2s_tdm_clk_compensation_get,
	.put = rockchip_i2s_tdm_clk_compensation_put,
};

/* loopback mode select */
enum {
	LOOPBACK_MODE_DIS = 0,
	LOOPBACK_MODE_1,
	LOOPBACK_MODE_2,
	LOOPBACK_MODE_2_SWAP,
};

static const char *const loopback_text[] = {
	"Disabled",
	"Mode1",
	"Mode2",
	"Mode2 Swap",
};

static SOC_ENUM_SINGLE_EXT_DECL(loopback_mode, loopback_text);

static int rockchip_i2s_tdm_loopback_get(struct snd_kcontrol *kcontrol,
					 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);
	unsigned int reg = 0, mode = 0;

	pm_runtime_get_sync(component->dev);
	regmap_read(i2s_tdm->regmap, I2S_XFER, &reg);
	pm_runtime_put(component->dev);

	switch (reg & I2S_XFER_LP_MODE_MASK) {
	case I2S_XFER_LP_MODE_2_SWAP:
		mode = LOOPBACK_MODE_2_SWAP;
		break;
	case I2S_XFER_LP_MODE_2:
		mode = LOOPBACK_MODE_2;
		break;
	case I2S_XFER_LP_MODE_1:
		mode = LOOPBACK_MODE_1;
		break;
	default:
		mode = LOOPBACK_MODE_DIS;
		break;
	}

	ucontrol->value.enumerated.item[0] = mode;

	return 0;
}

static int rockchip_i2s_tdm_loopback_put(struct snd_kcontrol *kcontrol,
					 struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);
	unsigned int val = 0, mode = ucontrol->value.enumerated.item[0];

	if (mode < LOOPBACK_MODE_DIS ||
	    mode > LOOPBACK_MODE_2_SWAP)
		return -EINVAL;

	switch (mode) {
	case LOOPBACK_MODE_2_SWAP:
		val = I2S_XFER_LP_MODE_2_SWAP;
		break;
	case LOOPBACK_MODE_2:
		val = I2S_XFER_LP_MODE_2;
		break;
	case LOOPBACK_MODE_1:
		val = I2S_XFER_LP_MODE_1;
		break;
	default:
		val = I2S_XFER_LP_MODE_DIS;
		break;
	}

	pm_runtime_get_sync(component->dev);
	regmap_update_bits(i2s_tdm->regmap, I2S_XFER, I2S_XFER_LP_MODE_MASK, val);
	pm_runtime_put(component->dev);

	return 0;
}

static const char * const rpaths_text[] = {
	"From SDI0", "From SDI1", "From SDI2", "From SDI3" };

static const char * const tpaths_text[] = {
	"From PATH0", "From PATH1", "From PATH2", "From PATH3" };

/* TXCR */
static SOC_ENUM_SINGLE_DECL(tpath3_enum, I2S_TXCR, 29, tpaths_text);
static SOC_ENUM_SINGLE_DECL(tpath2_enum, I2S_TXCR, 27, tpaths_text);
static SOC_ENUM_SINGLE_DECL(tpath1_enum, I2S_TXCR, 25, tpaths_text);
static SOC_ENUM_SINGLE_DECL(tpath0_enum, I2S_TXCR, 23, tpaths_text);

/* RXCR */
static SOC_ENUM_SINGLE_DECL(rpath3_enum, I2S_RXCR, 23, rpaths_text);
static SOC_ENUM_SINGLE_DECL(rpath2_enum, I2S_RXCR, 21, rpaths_text);
static SOC_ENUM_SINGLE_DECL(rpath1_enum, I2S_RXCR, 19, rpaths_text);
static SOC_ENUM_SINGLE_DECL(rpath0_enum, I2S_RXCR, 17, rpaths_text);

static int rockchip_i2s_tdm_wait_time_info(struct snd_kcontrol *kcontrol,
					   struct snd_ctl_elem_info *uinfo)
{
	uinfo->type = SNDRV_CTL_ELEM_TYPE_INTEGER;
	uinfo->count = 1;
	uinfo->value.integer.min = 0;
	uinfo->value.integer.max = WAIT_TIME_MS_MAX;
	uinfo->value.integer.step = 1;

	return 0;
}

static int rockchip_i2s_tdm_rd_wait_time_get(struct snd_kcontrol *kcontrol,
					     struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);

	ucontrol->value.integer.value[0] = i2s_tdm->wait_time[SNDRV_PCM_STREAM_CAPTURE];

	return 0;
}

static int rockchip_i2s_tdm_rd_wait_time_put(struct snd_kcontrol *kcontrol,
					     struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);

	if (ucontrol->value.integer.value[0] > WAIT_TIME_MS_MAX)
		return -EINVAL;

	i2s_tdm->wait_time[SNDRV_PCM_STREAM_CAPTURE] = ucontrol->value.integer.value[0];

	return 1;
}

static int rockchip_i2s_tdm_wr_wait_time_get(struct snd_kcontrol *kcontrol,
					     struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);

	ucontrol->value.integer.value[0] = i2s_tdm->wait_time[SNDRV_PCM_STREAM_PLAYBACK];

	return 0;
}

static int rockchip_i2s_tdm_wr_wait_time_put(struct snd_kcontrol *kcontrol,
					     struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_component *component = snd_soc_kcontrol_component(kcontrol);
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);

	if (ucontrol->value.integer.value[0] > WAIT_TIME_MS_MAX)
		return -EINVAL;

	i2s_tdm->wait_time[SNDRV_PCM_STREAM_PLAYBACK] = ucontrol->value.integer.value[0];

	return 1;
}

#define SAI_PCM_WAIT_TIME(xname, xhandler_get, xhandler_put)	\
{	.iface = SNDRV_CTL_ELEM_IFACE_PCM, .name = xname,	\
	.info = rockchip_i2s_tdm_wait_time_info,			\
	.get = xhandler_get, .put = xhandler_put }

static const struct snd_kcontrol_new rockchip_i2s_tdm_snd_controls[] = {
	SOC_ENUM("Receive PATH3 Source Select", rpath3_enum),
	SOC_ENUM("Receive PATH2 Source Select", rpath2_enum),
	SOC_ENUM("Receive PATH1 Source Select", rpath1_enum),
	SOC_ENUM("Receive PATH0 Source Select", rpath0_enum),
	SOC_ENUM("Transmit SDO3 Source Select", tpath3_enum),
	SOC_ENUM("Transmit SDO2 Source Select", tpath2_enum),
	SOC_ENUM("Transmit SDO1 Source Select", tpath1_enum),
	SOC_ENUM("Transmit SDO0 Source Select", tpath0_enum),

	SOC_ENUM_EXT("I2STDM Digital Loopback Mode", loopback_mode,
		     rockchip_i2s_tdm_loopback_get,
		     rockchip_i2s_tdm_loopback_put),
#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
	SOC_ENUM_EXT("Transmit SDOx Select", tx_lanes_enum,
		     rockchip_i2s_tdm_tx_lanes_get, rockchip_i2s_tdm_tx_lanes_put),
	SOC_ENUM_EXT("Receive SDIx Select", rx_lanes_enum,
		     rockchip_i2s_tdm_rx_lanes_get, rockchip_i2s_tdm_rx_lanes_put),
#endif
	SAI_PCM_WAIT_TIME("PCM Read Wait Time MS",
			  rockchip_i2s_tdm_rd_wait_time_get,
			  rockchip_i2s_tdm_rd_wait_time_put),
	SAI_PCM_WAIT_TIME("PCM Write Wait Time MS",
			  rockchip_i2s_tdm_wr_wait_time_get,
			  rockchip_i2s_tdm_wr_wait_time_put),
};

static int rockchip_i2s_tdm_dai_probe(struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_dai_get_drvdata(dai);

	dai->capture_dma_data = &i2s_tdm->capture_dma_data;
	dai->playback_dma_data = &i2s_tdm->playback_dma_data;

	if (i2s_tdm->mclk_calibrate)
		snd_soc_add_component_controls(dai->component,
					       &rockchip_i2s_tdm_compensation_control,
					       1);

	return 0;
}

static int rockchip_dai_tdm_slot(struct snd_soc_dai *dai,
				 unsigned int tx_mask, unsigned int rx_mask,
				 int slots, int slot_width)
{
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_dai_get_drvdata(dai);
	unsigned int mask, val;

	if (i2s_tdm->checked_lifecycle)
		return -EOPNOTSUPP;

	i2s_tdm->tdm_mode = true;
	i2s_tdm->bclk_fs = slots * slot_width;
	mask = TDM_SLOT_BIT_WIDTH_MSK | TDM_FRAME_WIDTH_MSK;
	val = TDM_SLOT_BIT_WIDTH(slot_width) |
	      TDM_FRAME_WIDTH(slots * slot_width);
	pm_runtime_get_sync(dai->dev);
	regmap_update_bits(i2s_tdm->regmap, I2S_TDM_TXCR,
			   mask, val);
	regmap_update_bits(i2s_tdm->regmap, I2S_TDM_RXCR,
			   mask, val);
	pm_runtime_put(dai->dev);

	return 0;
}


static int i2s_checked_prepare(struct snd_pcm_substream *substream,
			       struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned long flags;
	int ret;

	if (!i2s_tdm->checked_lifecycle)
		return 0;
	if (i2s_tdm->shared_params_enabled) {
		int stream = substream->stream;
		if (stream != 0 && stream != 1)
			return -EINVAL;
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		ret = i2s_tdm->runtime_error;
		if (!ret && i2s_tdm->shutting_down)
			ret = -ESHUTDOWN;
		if (!ret && !READ_ONCE(i2s_tdm->ready))
			ret = -EAGAIN;
		if (!ret && (i2s_tdm->configuring || i2s_tdm->power_transition ||
			i2s_tdm->shared_params.pending || i2s_tdm->starting ||
			(i2s_tdm->started & BIT(stream))))
			ret = -EBUSY;
		if (!ret && (i2s_tdm->substreams[stream] != substream ||
			i2s_tdm->shared_params.owner[stream] != substream))
			ret = -EINVAL;
		if (!ret) {
			if (i2s_tdm->started)
				ret = i2s_trcm_quiet_locked(i2s_tdm, stream);
			else
				ret = i2s_trcm_joint_locked(i2s_tdm, 0);
			if (ret)
				i2s_trcm_joint_locked(i2s_tdm, ret);
		}
		if (!ret)
			i2s_tdm->shared_prepared |= BIT(stream);
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		return ret;
	}
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	ret = i2s_checked_gate_locked(i2s_tdm);
	if (!ret && !READ_ONCE(i2s_tdm->ready))
		ret = -EAGAIN;
	if (!ret)
		ret = i2s_checked_stop_locked(i2s_tdm, substream->stream, true);
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	return ret;
}

static int i2s_checked_component_trigger(struct snd_soc_component *component,
					 struct snd_pcm_substream *substream,
					 int cmd)
{
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_component_get_drvdata(component);
	unsigned long flags;
	int ret = 0;

	if (!i2s_tdm->checked_lifecycle)
		return 0;
	if (substream->stream != 0 && substream->stream != 1)
		return -EINVAL;
	if (i2s_tdm->shared_params_enabled) {
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		switch (cmd) {
		case SNDRV_PCM_TRIGGER_START:
		case SNDRV_PCM_TRIGGER_RESUME:
		case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
			ret = i2s_trcm_admit_locked(i2s_tdm, substream);
			break;
		case SNDRV_PCM_TRIGGER_STOP:
		case SNDRV_PCM_TRIGGER_SUSPEND:
		case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
			ret = i2s_trcm_stop_locked(i2s_tdm, substream, true);
			break;
		default:
			ret = -EINVAL;
			break;
		}
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		return ret;
	}
	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		if (i2s_tdm->runtime_error)
			ret = i2s_tdm->runtime_error;
		else if (i2s_tdm->shutting_down)
			ret = -ESHUTDOWN;
		else if (!READ_ONCE(i2s_tdm->ready))
			ret = -EAGAIN;
		else if (i2s_tdm->configuring || i2s_tdm->power_transition || (i2s_tdm->started & ~BIT(substream->stream)) ||
			 (!i2s_tdm->started && !i2s_tdm->stop_proven))
			ret = -EBUSY;
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		break;
	default:
		break;
	}
	return ret;
}

static int rockchip_i2s_tdm_startup(struct snd_pcm_substream *substream,
				    struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_dai_get_drvdata(dai);
	int stream = substream->stream;
	unsigned long flags;
	int ret;

	if (i2s_tdm->checked_lifecycle) {
		if (stream != SNDRV_PCM_STREAM_PLAYBACK && stream != SNDRV_PCM_STREAM_CAPTURE)
			return -EINVAL;
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		ret = i2s_checked_gate_locked(i2s_tdm);
		if (!ret && !READ_ONCE(i2s_tdm->ready))
			ret = -EAGAIN;
		if (!ret && READ_ONCE(i2s_tdm->substreams[stream]))
			ret = -EBUSY;
		if (!ret) {
			if (i2s_tdm->wait_time[stream])
				substream->wait_time = msecs_to_jiffies(i2s_tdm->wait_time[stream]);
			i2s_tdm->irq_drained = false;
			WRITE_ONCE(i2s_tdm->substreams[stream], substream);
		}
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		return ret;
	}

	if (i2s_tdm->substreams[stream])
		return -EBUSY;

	if (i2s_tdm->wait_time[stream])
		substream->wait_time = msecs_to_jiffies(i2s_tdm->wait_time[stream]);

	i2s_tdm->substreams[stream] = substream;

	return 0;
}

static void rockchip_i2s_tdm_shutdown(struct snd_pcm_substream *substream,
				      struct snd_soc_dai *dai)
{
	struct rk_i2s_tdm_dev *i2s_tdm = snd_soc_dai_get_drvdata(dai);
	unsigned long flags;
	int ret = 0;

	if (i2s_tdm->checked_lifecycle) {
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		if (i2s_tdm->shared_params_enabled) {
			if ((substream->stream != 0 && substream->stream != 1) ||
			    i2s_tdm->substreams[substream->stream] != substream) {
				spin_unlock_irqrestore(&i2s_tdm->lock, flags);
				return;
			}
			/* Legal ALSA close is serialized against its own trigger/GO call. */
			if (i2s_tdm->starting & BIT(substream->stream)) {
				spin_unlock_irqrestore(&i2s_tdm->lock, flags);
				i2s_checked_failstop(i2s_tdm, "close with C3 GO borrow", -EBUSY);
				return;
			}
			ret = i2s_trcm_stop_locked(i2s_tdm, substream, false);
		} else if (!(i2s_tdm->started & ~BIT(substream->stream))) {
			ret = i2s_checked_stop_locked(i2s_tdm, substream->stream, true);
		}
		if (READ_ONCE(i2s_tdm->substreams[substream->stream]) == substream)
			WRITE_ONCE(i2s_tdm->substreams[substream->stream], NULL);
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		if (i2s_tdm->irq > 0)
			synchronize_irq(i2s_tdm->irq);
		spin_lock_irqsave(&i2s_tdm->lock, flags);
		if (!READ_ONCE(i2s_tdm->substreams[0]) && !READ_ONCE(i2s_tdm->substreams[1]) && !i2s_tdm->starting)
			i2s_tdm->irq_drained = true;
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		if (!ret && i2s_tdm->shared_params_enabled)
			ret = i2s_shared_free(substream, dai);
		if (ret)
			dev_err(i2s_tdm->dev, "CPU shutdown stop unproved: %d\n", ret);
		return;
	}
	i2s_tdm->substreams[substream->stream] = NULL;
}

static const struct snd_soc_dai_ops rockchip_i2s_tdm_dai_ops = {
	.startup = rockchip_i2s_tdm_startup,
	.shutdown = rockchip_i2s_tdm_shutdown,
	.prepare = i2s_checked_prepare,
	.hw_params = rockchip_i2s_tdm_hw_params,
	.set_sysclk = rockchip_i2s_tdm_set_sysclk,
	.set_fmt = rockchip_i2s_tdm_set_fmt,
	.set_tdm_slot = rockchip_dai_tdm_slot,
	.trigger = rockchip_i2s_tdm_trigger,
};

static void i2s_trcm_async_fault(struct snd_soc_dai *dai, int first_errno)
{
	struct rk_i2s_tdm_dev *i2s_tdm = to_info(dai);
	unsigned long flags;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	i2s_checked_error_locked(i2s_tdm, first_errno < 0 ? first_errno : -EIO);
	/* The sink may observe a PM transition or a retained late callback. A RAM
	 * latch cannot manufacture MMIO ownership or a STOP proof in that state.
	 */
	if (!i2s_tdm->hclk_enabled || !i2s_tdm->mclks_enabled || i2s_tdm->power_transition) {
		if (i2s_tdm->transport_live || i2s_tdm->started || i2s_tdm->starting)
			i2s_tdm->stop_proven = false;
		spin_unlock_irqrestore(&i2s_tdm->lock, flags);
		return;
	}
	i2s_trcm_joint_locked(i2s_tdm, first_errno < 0 ? first_errno : -EIO);
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
}

static const struct snd_soc_dai_ops rockchip_i2s_tdm_shared_ops = {
	.startup = rockchip_i2s_tdm_startup,
	.shutdown = rockchip_i2s_tdm_shutdown,
	.prepare = i2s_checked_prepare,
	.hw_params = i2s_shared_hw_params,
	.hw_free = i2s_shared_free,
	.set_sysclk = i2s_shared_sysclk,
	.set_fmt = i2s_shared_set_fmt,
	.set_tdm_slot = i2s_shared_tdm_slot,
	.trigger = rockchip_i2s_tdm_trigger,
	.hw_params_begin = i2s_shared_begin,
	.hw_params_commit = i2s_shared_commit,
	.hw_params_abort = i2s_shared_abort,
	.hw_params_reuse = i2s_shared_reuse,
	.hw_params_free_check = i2s_shared_free_check,
	.hw_params_fault = i2s_shared_fault,
	.pcm_async_fault = i2s_trcm_async_fault,
};

static const struct snd_soc_component_driver rockchip_i2s_tdm_component = {
	.name = DRV_NAME,
	.trigger = i2s_checked_component_trigger,
	.controls = rockchip_i2s_tdm_snd_controls,
	.num_controls = ARRAY_SIZE(rockchip_i2s_tdm_snd_controls),
};

/* The checked single-direction profile exposes only readonly lifecycle state. */
static const struct snd_soc_component_driver rockchip_i2s_tdm_checked_component = {
	.name = DRV_NAME,
	.trigger = i2s_checked_component_trigger,
};

static bool rockchip_i2s_tdm_wr_reg(struct device *dev, unsigned int reg)
{
	switch (reg) {
	case I2S_TXCR:
	case I2S_RXCR:
	case I2S_CKR:
	case I2S_DMACR:
	case I2S_INTCR:
	case I2S_XFER:
	case I2S_CLR:
	case I2S_TXDR:
	case I2S_TDM_TXCR:
	case I2S_TDM_RXCR:
	case I2S_CLKDIV:
		return true;
	default:
		return false;
	}
}

static bool rockchip_i2s_tdm_rd_reg(struct device *dev, unsigned int reg)
{
	switch (reg) {
	case I2S_TXCR:
	case I2S_RXCR:
	case I2S_CKR:
	case I2S_DMACR:
	case I2S_INTCR:
	case I2S_XFER:
	case I2S_CLR:
	case I2S_TXDR:
	case I2S_RXDR:
	case I2S_TXFIFOLR:
	case I2S_INTSR:
	case I2S_RXFIFOLR:
	case I2S_TDM_TXCR:
	case I2S_TDM_RXCR:
	case I2S_CLKDIV:
		return true;
	default:
		return false;
	}
}

static bool rockchip_i2s_tdm_volatile_reg(struct device *dev, unsigned int reg)
{
	switch (reg) {
	case I2S_TXFIFOLR:
	case I2S_INTCR:
	case I2S_INTSR:
	case I2S_CLR:
	case I2S_TXDR:
	case I2S_RXDR:
	case I2S_RXFIFOLR:
		return true;
	default:
		return false;
	}
}

static bool rockchip_i2s_tdm_precious_reg(struct device *dev, unsigned int reg)
{
	switch (reg) {
	case I2S_RXDR:
		return true;
	default:
		return false;
	}
}

static const struct reg_default rockchip_i2s_tdm_reg_defaults[] = {
	{0x00, 0x7200000f},
	{0x04, 0x01c8000f},
	{0x08, 0x00001f1f},
	{0x10, 0x001f0000},
	{0x14, 0x01f00000},
	{0x30, 0x00003eff},
	{0x34, 0x00003eff},
	{0x38, 0x00000707},
};

static const struct regmap_config rockchip_i2s_tdm_regmap_config = {
	.reg_bits = 32,
	.reg_stride = 4,
	.val_bits = 32,
	.max_register = I2S_CLKDIV,
	.reg_defaults = rockchip_i2s_tdm_reg_defaults,
	.num_reg_defaults = ARRAY_SIZE(rockchip_i2s_tdm_reg_defaults),
	.writeable_reg = rockchip_i2s_tdm_wr_reg,
	.readable_reg = rockchip_i2s_tdm_rd_reg,
	.volatile_reg = rockchip_i2s_tdm_volatile_reg,
	.precious_reg = rockchip_i2s_tdm_precious_reg,
	.cache_type = REGCACHE_FLAT,
};

static int common_soc_init(struct device *dev, u32 addr)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);
	const struct txrx_config *configs = i2s_tdm->soc_data->configs;
	u32 reg = 0, val = 0, trcm = i2s_tdm->clk_trcm;
	int i;

	if (IS_ERR(i2s_tdm->grf))
		return i2s_tdm->checked_lifecycle ? PTR_ERR(i2s_tdm->grf) : 0;

	switch (trcm) {
	case I2S_CKR_TRCM_TXONLY:
	case I2S_CKR_TRCM_RXONLY:
		break;
	default:
		return 0;
	}

	for (i = 0; i < i2s_tdm->soc_data->config_count; i++) {
		if (addr != configs[i].addr)
			continue;
		reg = configs[i].reg;
		if (trcm == I2S_CKR_TRCM_TXONLY)
			val = configs[i].txonly;
		else
			val = configs[i].rxonly;

		if (reg) {
			int ret = regmap_write(i2s_tdm->grf, reg, val);
			if (i2s_tdm->checked_lifecycle && ret)
				return ret;
		}
	}

	return 0;
}

static const struct txrx_config px30_txrx_config[] = {
	{ 0xff060000, 0x184, PX30_I2S0_CLK_TXONLY, PX30_I2S0_CLK_RXONLY },
};

static const struct txrx_config rk1808_txrx_config[] = {
	{ 0xff7e0000, 0x190, RK1808_I2S0_CLK_TXONLY, RK1808_I2S0_CLK_RXONLY },
};

static const struct txrx_config rk3308_txrx_config[] = {
	{ 0xff300000, 0x308, RK3308_I2S0_CLK_TXONLY, RK3308_I2S0_CLK_RXONLY },
	{ 0xff310000, 0x308, RK3308_I2S1_CLK_TXONLY, RK3308_I2S1_CLK_RXONLY },
};

static const struct txrx_config rk3568_txrx_config[] = {
	{ 0xfe410000, 0x504, RK3568_I2S1_CLK_TXONLY, RK3568_I2S1_CLK_RXONLY },
	{ 0xfe430000, 0x504, RK3568_I2S3_CLK_TXONLY, RK3568_I2S3_CLK_RXONLY },
	{ 0xfe430000, 0x508, RK3568_I2S3_MCLK_TXONLY, RK3568_I2S3_MCLK_RXONLY },
};

static const struct txrx_config rv1126_txrx_config[] = {
	{ 0xff800000, 0x10260, RV1126_I2S0_CLK_TXONLY, RV1126_I2S0_CLK_RXONLY },
};

static const struct rk_i2s_soc_data px30_i2s_soc_data = {
	.softrst_offset = 0x0300,
	.configs = px30_txrx_config,
	.config_count = ARRAY_SIZE(px30_txrx_config),
	.init = common_soc_init,
};

static const struct rk_i2s_soc_data rk1808_i2s_soc_data = {
	.softrst_offset = 0x0300,
	.configs = rk1808_txrx_config,
	.config_count = ARRAY_SIZE(rk1808_txrx_config),
	.init = common_soc_init,
};

static const struct rk_i2s_soc_data rk3308_i2s_soc_data = {
	.softrst_offset = 0x0400,
	.grf_reg_offset = 0x0308,
	.grf_shift = 5,
	.configs = rk3308_txrx_config,
	.config_count = ARRAY_SIZE(rk3308_txrx_config),
	.init = common_soc_init,
};

static const struct rk_i2s_soc_data rk3568_i2s_soc_data = {
	.softrst_offset = 0x0400,
	.configs = rk3568_txrx_config,
	.config_count = ARRAY_SIZE(rk3568_txrx_config),
	.init = common_soc_init,
};

static const struct rk_i2s_soc_data rv1126_i2s_soc_data = {
	.softrst_offset = 0x0300,
	.configs = rv1126_txrx_config,
	.config_count = ARRAY_SIZE(rv1126_txrx_config),
	.init = common_soc_init,
};

static const struct of_device_id rockchip_i2s_tdm_match[] = {
#ifdef CONFIG_CPU_PX30
	{ .compatible = "rockchip,px30-i2s-tdm", .data = &px30_i2s_soc_data },
#endif
#ifdef CONFIG_CPU_RK1808
	{ .compatible = "rockchip,rk1808-i2s-tdm", .data = &rk1808_i2s_soc_data },
#endif
#ifdef CONFIG_CPU_RK3308
	{ .compatible = "rockchip,rk3308-i2s-tdm", .data = &rk3308_i2s_soc_data },
#endif
#ifdef CONFIG_CPU_RK3568
	{ .compatible = "rockchip,rk3568-i2s-tdm", .data = &rk3568_i2s_soc_data },
#endif
#ifdef CONFIG_CPU_RK3588
	{ .compatible = "rockchip,rk3588-i2s-tdm", },
#endif
#ifdef CONFIG_CPU_RV1106
	{ .compatible = "rockchip,rv1106-i2s-tdm", },
#endif
#ifdef CONFIG_CPU_RV1126
	{ .compatible = "rockchip,rv1126-i2s-tdm", .data = &rv1126_i2s_soc_data },
#endif
	{},
};

#ifdef HAVE_SYNC_RESET
static int of_i2s_resetid_get(struct device_node *node,
			      const char *id)
{
	struct of_phandle_args args;
	int index = 0;
	int ret;

	if (id)
		index = of_property_match_string(node,
						 "reset-names", id);
	ret = of_parse_phandle_with_args(node, "resets", "#reset-cells",
					 index, &args);
	if (ret)
		return ret;

	return args.args[0];
}
#endif

static int rockchip_i2s_tdm_dai_prepare(struct platform_device *pdev,
					struct snd_soc_dai_driver **soc_dai)
{
	struct snd_soc_dai_driver rockchip_i2s_tdm_dai = {
		.probe = rockchip_i2s_tdm_dai_probe,
		.playback = {
			.stream_name = "Playback",
			.channels_min = 2,
			.channels_max = 64,
			.rates = SNDRV_PCM_RATE_8000_192000,
			.formats = (SNDRV_PCM_FMTBIT_S8 |
				    SNDRV_PCM_FMTBIT_S16_LE |
				    SNDRV_PCM_FMTBIT_S20_3LE |
				    SNDRV_PCM_FMTBIT_S24_LE |
				    SNDRV_PCM_FMTBIT_S32_LE |
				    SNDRV_PCM_FMTBIT_IEC958_SUBFRAME_LE),
		},
		.capture = {
			.stream_name = "Capture",
			.channels_min = 2,
			.channels_max = 64,
			.rates = SNDRV_PCM_RATE_8000_192000,
			.formats = (SNDRV_PCM_FMTBIT_S8 |
				    SNDRV_PCM_FMTBIT_S16_LE |
				    SNDRV_PCM_FMTBIT_S20_3LE |
				    SNDRV_PCM_FMTBIT_S24_LE |
				    SNDRV_PCM_FMTBIT_S32_LE |
				    SNDRV_PCM_FMTBIT_IEC958_SUBFRAME_LE),
		},
		.ops = &rockchip_i2s_tdm_dai_ops,
	};

	*soc_dai = devm_kmemdup(&pdev->dev, &rockchip_i2s_tdm_dai,
				sizeof(rockchip_i2s_tdm_dai), GFP_KERNEL);
	if (!(*soc_dai))
		return -ENOMEM;

	return 0;
}

static int rockchip_i2s_tdm_path_check(struct rk_i2s_tdm_dev *i2s_tdm,
				       int num,
				       bool is_rx_path)
{
	unsigned int *i2s_data;
	int i, j, ret = 0;

	if (is_rx_path)
		i2s_data = i2s_tdm->i2s_sdis;
	else
		i2s_data = i2s_tdm->i2s_sdos;

	for (i = 0; i < num; i++) {
		if (i2s_data[i] > CH_GRP_MAX - 1) {
			dev_err(i2s_tdm->dev,
				"%s path i2s_data[%d]: %d is overflow, max is: %d\n",
				is_rx_path ? "RX" : "TX",
				i, i2s_data[i], CH_GRP_MAX);
			ret = -EINVAL;
			goto err;
		}

		for (j = 0; j < num; j++) {
			if (i == j)
				continue;

			if (i2s_data[i] == i2s_data[j]) {
				dev_err(i2s_tdm->dev,
					"%s path invalid routed i2s_data: [%d]%d == [%d]%d\n",
					is_rx_path ? "RX" : "TX",
					i, i2s_data[i],
					j, i2s_data[j]);
				ret = -EINVAL;
				goto err;
			}
		}
	}

err:
	return ret;
}

static void rockchip_i2s_tdm_tx_path_config(struct rk_i2s_tdm_dev *i2s_tdm,
					    int num)
{
	int idx;

	for (idx = 0; idx < num; idx++) {
		regmap_update_bits(i2s_tdm->regmap, I2S_TXCR,
				   I2S_TXCR_PATH_MASK(idx),
				   I2S_TXCR_PATH(idx, i2s_tdm->i2s_sdos[idx]));
	}
}

static void rockchip_i2s_tdm_rx_path_config(struct rk_i2s_tdm_dev *i2s_tdm,
					    int num)
{
	int idx;

	for (idx = 0; idx < num; idx++) {
		regmap_update_bits(i2s_tdm->regmap, I2S_RXCR,
				   I2S_RXCR_PATH_MASK(idx),
				   I2S_RXCR_PATH(idx, i2s_tdm->i2s_sdis[idx]));
	}
}

static void rockchip_i2s_tdm_path_config(struct rk_i2s_tdm_dev *i2s_tdm,
					 int num, bool is_rx_path)
{
	if (is_rx_path)
		rockchip_i2s_tdm_rx_path_config(i2s_tdm, num);
	else
		rockchip_i2s_tdm_tx_path_config(i2s_tdm, num);
}

static int rockchip_i2s_tdm_path_prepare(struct rk_i2s_tdm_dev *i2s_tdm,
					 struct device_node *np,
					 bool is_rx_path)
{
	char *i2s_tx_path_prop = "rockchip,i2s-tx-route";
	char *i2s_rx_path_prop = "rockchip,i2s-rx-route";
	char *i2s_path_prop;
	unsigned int *i2s_data;
	int num, ret = 0;

	if (is_rx_path) {
		i2s_path_prop = i2s_rx_path_prop;
		i2s_data = i2s_tdm->i2s_sdis;
	} else {
		i2s_path_prop = i2s_tx_path_prop;
		i2s_data = i2s_tdm->i2s_sdos;
	}

	num = of_count_phandle_with_args(np, i2s_path_prop, NULL);
	if (num < 0) {
		if (num != -ENOENT) {
			dev_err(i2s_tdm->dev,
				"Failed to read '%s' num: %d\n",
				i2s_path_prop, num);
			ret = num;
		}
		goto out;
	} else if (num != CH_GRP_MAX) {
		dev_err(i2s_tdm->dev,
			"The num: %d should be: %d\n", num, CH_GRP_MAX);
		ret = -EINVAL;
		goto out;
	}

	ret = of_property_read_u32_array(np, i2s_path_prop,
					 i2s_data, num);
	if (ret < 0) {
		dev_err(i2s_tdm->dev,
			"Failed to read '%s': %d\n",
			i2s_path_prop, ret);
		goto out;
	}

	ret = rockchip_i2s_tdm_path_check(i2s_tdm, num, is_rx_path);
	if (ret < 0) {
		dev_err(i2s_tdm->dev,
			"Failed to check i2s data bus: %d\n", ret);
		goto out;
	}

	rockchip_i2s_tdm_path_config(i2s_tdm, num, is_rx_path);

out:
	return ret;
}

static int rockchip_i2s_tdm_tx_path_prepare(struct rk_i2s_tdm_dev *i2s_tdm,
					    struct device_node *np)
{
	return rockchip_i2s_tdm_path_prepare(i2s_tdm, np, 0);
}

static int rockchip_i2s_tdm_rx_path_prepare(struct rk_i2s_tdm_dev *i2s_tdm,
					    struct device_node *np)
{
	return rockchip_i2s_tdm_path_prepare(i2s_tdm, np, 1);
}

static int rockchip_i2s_tdm_get_fifo_count(struct device *dev, int stream)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);
	int val = 0;

	if (stream == SNDRV_PCM_STREAM_PLAYBACK)
		regmap_read(i2s_tdm->regmap, I2S_TXFIFOLR, &val);
	else
		regmap_read(i2s_tdm->regmap, I2S_RXFIFOLR, &val);

	val = ((val & I2S_FIFOLR_TFL3_MASK) >> I2S_FIFOLR_TFL3_SHIFT) +
	      ((val & I2S_FIFOLR_TFL2_MASK) >> I2S_FIFOLR_TFL2_SHIFT) +
	      ((val & I2S_FIFOLR_TFL1_MASK) >> I2S_FIFOLR_TFL1_SHIFT) +
	      ((val & I2S_FIFOLR_TFL0_MASK) >> I2S_FIFOLR_TFL0_SHIFT);

	return val;
}

static const struct snd_dlp_config dconfig = {
	.get_fifo_count = rockchip_i2s_tdm_get_fifo_count,
};


static irqreturn_t i2s_trcm_isr(struct rk_i2s_tdm_dev *i2s_tdm)
{
	struct snd_pcm_substream *playback = NULL, *capture = NULL;
	unsigned long flags;
	unsigned int val = 0, owners;
	int ret;
	irqreturn_t result = IRQ_NONE;

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (!i2s_tdm->irq_live || i2s_tdm->shutting_down)
		goto unlock;
	ret = regmap_read(i2s_tdm->regmap, I2S_INTSR, &val);
	if (!ret && !(val & (I2S_INTSR_TXUI_ACT | I2S_INTSR_RXOI_ACT)))
		goto unlock;
	owners = i2s_tdm->started | i2s_tdm->starting;
	if (!ret && (val & I2S_INTSR_TXUI_ACT)) {
		ret = i2s_checked_irq_locked(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK, false);
		if (owners & BIT(SNDRV_PCM_STREAM_PLAYBACK))
			playback = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_PLAYBACK]);
		result = IRQ_HANDLED;
	}
	if (!ret && (val & I2S_INTSR_RXOI_ACT)) {
		ret = i2s_checked_irq_locked(i2s_tdm, SNDRV_PCM_STREAM_CAPTURE, false);
		if (owners & BIT(SNDRV_PCM_STREAM_CAPTURE))
			capture = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_CAPTURE]);
		result = IRQ_HANDLED;
	}
	if (ret) {
		/* A failed status read never interprets val. Failed shared IO affects both. */
		if (owners & BIT(SNDRV_PCM_STREAM_PLAYBACK))
			playback = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_PLAYBACK]);
		if (owners & BIT(SNDRV_PCM_STREAM_CAPTURE))
			capture = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_CAPTURE]);
		i2s_trcm_joint_locked(i2s_tdm, ret);
		result = IRQ_HANDLED;
	}
unlock:
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	/* Native START holds its stream lock through RUNNING publication. XRUN waits
	 * outside the CPU lock; close synchronize_irq drains captured pointer borrows.
	 */
	if (playback)
		snd_pcm_stop_xrun(playback);
	if (capture)
		snd_pcm_stop_xrun(capture);
	return result;
}

static irqreturn_t i2s_checked_isr(struct rk_i2s_tdm_dev *i2s_tdm)
{
	struct snd_pcm_substream *playback = NULL, *capture = NULL;
	unsigned long flags;
	unsigned int val = 0;
	int ret;
	irqreturn_t result = IRQ_NONE;

	if (i2s_tdm->shared_params_enabled)
		return i2s_trcm_isr(i2s_tdm);

	spin_lock_irqsave(&i2s_tdm->lock, flags);
	if (!i2s_tdm->irq_live || i2s_tdm->shutting_down)
		goto unlock;
	ret = regmap_read(i2s_tdm->regmap, I2S_INTSR, &val);
	if (ret) {
		i2s_checked_error_locked(i2s_tdm, ret);
		/* Do not interpret failed INTSR. Notify only the known active owner. */
		if (i2s_tdm->started & BIT(SNDRV_PCM_STREAM_PLAYBACK))
			playback = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_PLAYBACK]);
		if (i2s_tdm->started & BIT(SNDRV_PCM_STREAM_CAPTURE))
			capture = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_CAPTURE]);
		i2s_checked_irq_locked(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK, false);
		i2s_checked_irq_locked(i2s_tdm, SNDRV_PCM_STREAM_CAPTURE, false);
		result = IRQ_HANDLED;
		goto unlock;
	}
	if (val & I2S_INTSR_TXUI_ACT) {
		ret = i2s_checked_irq_locked(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK, false);
		i2s_checked_error_locked(i2s_tdm, ret);
		if (i2s_tdm->started & BIT(SNDRV_PCM_STREAM_PLAYBACK))
			playback = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_PLAYBACK]);
		result = IRQ_HANDLED;
	}
	if (val & I2S_INTSR_RXOI_ACT) {
		ret = i2s_checked_irq_locked(i2s_tdm, SNDRV_PCM_STREAM_CAPTURE, false);
		i2s_checked_error_locked(i2s_tdm, ret);
		if (i2s_tdm->started & BIT(SNDRV_PCM_STREAM_CAPTURE))
			capture = READ_ONCE(i2s_tdm->substreams[SNDRV_PCM_STREAM_CAPTURE]);
		result = IRQ_HANDLED;
	}
unlock:
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	/* ALSA takes stream locks then I2S lock. Never reverse this order. */
	if (playback)
		snd_pcm_stop_xrun(playback);
	if (capture)
		snd_pcm_stop_xrun(capture);
	return result;
}

static irqreturn_t rockchip_i2s_tdm_isr(int irq, void *devid)
{
	struct rk_i2s_tdm_dev *i2s_tdm = (struct rk_i2s_tdm_dev *)devid;
	struct snd_pcm_substream *substream;
	u32 val;

	if (i2s_tdm->checked_lifecycle)
		return i2s_checked_isr(i2s_tdm);

	regmap_read(i2s_tdm->regmap, I2S_INTSR, &val);
	if (val & I2S_INTSR_TXUI_ACT) {
		dev_warn_ratelimited(i2s_tdm->dev, "TX FIFO Underrun\n");
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_TXUIC, I2S_INTCR_TXUIC);
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_TXUIE_MASK,
				   I2S_INTCR_TXUIE(0));
		substream = i2s_tdm->substreams[SNDRV_PCM_STREAM_PLAYBACK];
		if (substream)
			snd_pcm_stop_xrun(substream);
	}

	if (val & I2S_INTSR_RXOI_ACT) {
		dev_warn_ratelimited(i2s_tdm->dev, "RX FIFO Overrun\n");
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_RXOIC, I2S_INTCR_RXOIC);
		regmap_update_bits(i2s_tdm->regmap, I2S_INTCR,
				   I2S_INTCR_RXOIE_MASK,
				   I2S_INTCR_RXOIE(0));
		substream = i2s_tdm->substreams[SNDRV_PCM_STREAM_CAPTURE];
		if (substream)
			snd_pcm_stop_xrun(substream);
	}

	return IRQ_HANDLED;
}

static int rockchip_i2s_tdm_keep_clk_always_on(struct rk_i2s_tdm_dev *i2s_tdm)
{
	unsigned int mclk_rate = DEFAULT_FS * DEFAULT_MCLK_FS;
	unsigned int bclk_rate = i2s_tdm->bclk_fs * DEFAULT_FS;
	unsigned int div_lrck = i2s_tdm->bclk_fs;
	unsigned int div_bclk;
	int ret;

	div_bclk = DIV_ROUND_CLOSEST(mclk_rate, bclk_rate);

	/* assign generic freq */
	clk_set_rate(i2s_tdm->mclk_rx, mclk_rate);
	clk_set_rate(i2s_tdm->mclk_tx, mclk_rate);

	ret = rockchip_i2s_tdm_mclk_reparent(i2s_tdm);
	if (ret)
		return ret;

	regmap_update_bits(i2s_tdm->regmap, I2S_CLKDIV,
			   I2S_CLKDIV_RXM_MASK | I2S_CLKDIV_TXM_MASK,
			   I2S_CLKDIV_RXM(div_bclk) | I2S_CLKDIV_TXM(div_bclk));
	regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
			   I2S_CKR_RSD_MASK | I2S_CKR_TSD_MASK,
			   I2S_CKR_RSD(div_lrck) | I2S_CKR_TSD(div_lrck));

	if (i2s_tdm->clk_trcm)
		rockchip_i2s_tdm_xfer_trcm_start(i2s_tdm);
	else
		rockchip_i2s_tdm_xfer_start(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK);

	pm_runtime_forbid(i2s_tdm->dev);

	dev_info(i2s_tdm->dev, "CLK-ALWAYS-ON: mclk: %d, bclk: %d, fsync: %d\n",
		 mclk_rate, bclk_rate, DEFAULT_FS);

	return 0;
}


static bool i2s_checked_profile(struct platform_device *pdev)
{
	struct device_node *node = pdev->dev.of_node;
	struct resource *res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	unsigned int val;

	if (!res || res->start != 0xfe410000 || resource_size(res) != 0x1000 ||
	    !of_device_is_compatible(node, "rockchip,rk3568-i2s-tdm") ||
	    of_property_read_u32(node, "rockchip,clk-trcm", &val) || val != 1)
		return false;
	if (!of_property_read_u32(node, "rockchip,bclk-fs", &val) && val != 64)
		return false;
	return !of_property_read_bool(node, "rockchip,always-on") &&
		!of_property_read_bool(node, "rockchip,hdmi-path") &&
		!of_property_read_bool(node, "rockchip,mclk-calibrate") &&
		!of_property_read_bool(node, "rockchip,io-multiplex") &&
		!of_property_read_bool(node, "rockchip,tdm-multi-lanes") &&
		!of_property_read_bool(node, "rockchip,no-dmaengine") &&
		!of_property_read_bool(node, "rockchip,digital-loopback") &&
		!of_find_property(node, "rockchip,i2s-tx-route", NULL) &&
		!of_find_property(node, "rockchip,i2s-rx-route", NULL);
}

static int i2s_checked_initial_config(struct rk_i2s_tdm_dev *i2s_tdm, u32 addr)
{
	int ret;

	ret = regmap_update_bits(i2s_tdm->regmap, I2S_DMACR,
				 I2S_DMACR_TDL_MASK, I2S_DMACR_TDL(16));
	if (ret)
		return ret;
	ret = regmap_update_bits(i2s_tdm->regmap, I2S_DMACR,
				 I2S_DMACR_RDL_MASK, I2S_DMACR_RDL(16));
	if (ret)
		return ret;
	ret = regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
				 I2S_CKR_TRCM_MASK, i2s_tdm->clk_trcm);
	if (ret)
		return ret;
	return i2s_tdm->soc_data->init(i2s_tdm->dev, addr);
}

static ssize_t rk3568_lifecycle_state_show(struct device *dev,
					struct device_attribute *attr, char *buf)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);
	unsigned long flags;
	unsigned int owners, open_mask, stopped, irq_live, irq_drained;
	unsigned int mclk_leases, hclk_lease, configuring, power_transition, shutting_down, ready;
	unsigned int stop_reads;
	int error;

	if (!i2s_tdm->checked_lifecycle)
		return -EOPNOTSUPP;
	spin_lock_irqsave(&i2s_tdm->lock, flags);
	owners = i2s_tdm->started;
	open_mask = (!!READ_ONCE(i2s_tdm->substreams[0])) |
		(!!READ_ONCE(i2s_tdm->substreams[1]) << 1);
	error = i2s_tdm->runtime_error;
	stopped = i2s_tdm->stop_proven;
	irq_live = i2s_tdm->irq_live;
	irq_drained = i2s_tdm->irq_drained;
	mclk_leases = READ_ONCE(i2s_tdm->mclks_enabled);
	hclk_lease = READ_ONCE(i2s_tdm->hclk_enabled);
	configuring = i2s_tdm->configuring;
	power_transition = i2s_tdm->power_transition;
	shutting_down = i2s_tdm->shutting_down;
	ready = READ_ONCE(i2s_tdm->ready);
	stop_reads = i2s_tdm->stop_reads;
	spin_unlock_irqrestore(&i2s_tdm->lock, flags);
	/* No MMIO, PM get, clock operation or pointer disclosure in this reader. */
	return sysfs_emit(buf, "version=1 ready=%u error=%d owners=%u open=%u stop_proven=%u stop_reads=%u irq_live=%u irq_drained=%u mclk_leases=%u hclk_lease=%u configuring=%u power_transition=%u shutting_down=%u\n",
		ready, error, owners, open_mask, stopped, stop_reads, irq_live, irq_drained,
		mclk_leases, hclk_lease, configuring, power_transition, shutting_down);
}

static DEVICE_ATTR_RO(rk3568_lifecycle_state);
static struct attribute *i2s_checked_attrs[] = {
	&dev_attr_rk3568_lifecycle_state.attr,
	NULL,
};
static const struct attribute_group i2s_checked_group = {
	.attrs = i2s_checked_attrs,
};

static int rockchip_i2s_tdm_probe(struct platform_device *pdev)
{
	struct device_node *node = pdev->dev.of_node;
	const struct of_device_id *of_id;
	struct rk_i2s_tdm_dev *i2s_tdm;
	struct snd_soc_dai_driver *soc_dai;
	struct resource *res;
	void __iomem *regs;
#ifdef HAVE_SYNC_RESET
	bool sync;
#endif
	int ret, val, i, irq;

	ret = rockchip_i2s_tdm_dai_prepare(pdev, &soc_dai);
	if (ret)
		return ret;

	i2s_tdm = devm_kzalloc(&pdev->dev, sizeof(*i2s_tdm), GFP_KERNEL);
	if (!i2s_tdm)
		return -ENOMEM;

	i2s_tdm->dev = &pdev->dev;
	i2s_tdm->lrck_ratio = 1;
	i2s_tdm->checked_lifecycle = i2s_checked_profile(pdev);
	i2s_tdm->shared_params_enabled = of_property_read_bool(node,
						"rockchip,checked-shared-params-48k");
	if (i2s_tdm->shared_params_enabled) {
		if (!i2s_tdm->checked_lifecycle)
			return -EINVAL;
		/* soc_dai is already a devm-owned per-probe copy. */
		soc_dai->ops = &rockchip_i2s_tdm_shared_ops;
	}

	of_id = of_match_device(rockchip_i2s_tdm_match, &pdev->dev);
	if (!of_id)
		return -EINVAL;

#ifdef CONFIG_SND_SOC_ROCKCHIP_I2S_TDM_MULTI_LANES
	i2s_tdm->is_tdm_multi_lanes =
		device_property_read_bool(i2s_tdm->dev, "rockchip,tdm-multi-lanes");

	if (i2s_tdm->is_tdm_multi_lanes) {
		struct device_node *clk_src_node = NULL;

		i2s_tdm->tx_lanes = 1;
		i2s_tdm->rx_lanes = 1;

		if (!device_property_read_u32(i2s_tdm->dev, "rockchip,tdm-tx-lanes", &val)) {
			if ((val >= 1) && (val <= 4))
				i2s_tdm->tx_lanes = val;
		}

		if (!device_property_read_u32(i2s_tdm->dev, "rockchip,tdm-rx-lanes", &val)) {
			if ((val >= 1) && (val <= 4))
				i2s_tdm->rx_lanes = val;
		}

		i2s_tdm->i2s_lrck_gpio = devm_gpiod_get_optional(&pdev->dev, "i2s-lrck", GPIOD_IN);
		if (IS_ERR(i2s_tdm->i2s_lrck_gpio)) {
			ret = PTR_ERR(i2s_tdm->i2s_lrck_gpio);
			dev_err(&pdev->dev, "Failed to get i2s_lrck_gpio %d\n", ret);
			return ret;
		}

		i2s_tdm->tdm_fsync_gpio = devm_gpiod_get_optional(&pdev->dev, "tdm-fsync", GPIOD_IN);
		if (IS_ERR(i2s_tdm->tdm_fsync_gpio)) {
			ret = PTR_ERR(i2s_tdm->tdm_fsync_gpio);
			dev_err(&pdev->dev, "Failed to get tdm_fsync_gpio %d\n", ret);
			return ret;
		}

		/* It's optional, required when use soc clk src, such as: i2s2_2ch */
		clk_src_node = of_parse_phandle(node, "rockchip,clk-src", 0);
		if (clk_src_node) {
			i2s_tdm->clk_src_base = of_iomap(clk_src_node, 0);
			if (!i2s_tdm->clk_src_base)
				return -ENOENT;

			i2s_tdm->clk_src_dai = rockchip_i2s_tdm_find_dai(clk_src_node);
			if (!i2s_tdm->clk_src_dai)
				return -EPROBE_DEFER;

			pm_runtime_forbid(i2s_tdm->clk_src_dai->dev);
		}

		dev_info(&pdev->dev, "Used as TDM_MULTI_LANES mode\n");
	}
#endif

	spin_lock_init(&i2s_tdm->lock);
	i2s_tdm->soc_data = (const struct rk_i2s_soc_data *)of_id->data;

	for (i = 0; i < ARRAY_SIZE(of_quirks); i++)
		if (of_property_read_bool(node, of_quirks[i].quirk))
			i2s_tdm->quirks |= of_quirks[i].id;

	i2s_tdm->bclk_fs = 64;
	if (!of_property_read_u32(node, "rockchip,bclk-fs", &val)) {
		if ((val >= 32) && (val % 2 == 0))
			i2s_tdm->bclk_fs = val;
	}

	i2s_tdm->clk_trcm = I2S_CKR_TRCM_TXRX;
	if (!of_property_read_u32(node, "rockchip,clk-trcm", &val)) {
		if (val >= 0 && val <= 2) {
			i2s_tdm->clk_trcm = val << I2S_CKR_TRCM_SHIFT;
			if (i2s_tdm->clk_trcm)
				soc_dai->symmetric_rates = 1;
		}
	}

	i2s_tdm->tdm_fsync_half_frame =
		of_property_read_bool(node, "rockchip,tdm-fsync-half-frame");

	if (of_property_read_bool(node, "rockchip,playback-only"))
		soc_dai->capture.channels_min = 0;
	else if (of_property_read_bool(node, "rockchip,capture-only"))
		soc_dai->playback.channels_min = 0;

	i2s_tdm->grf = syscon_regmap_lookup_by_phandle(node, "rockchip,grf");

	i2s_tdm->pinctrl = devm_pinctrl_get(&pdev->dev);
	if (!IS_ERR_OR_NULL(i2s_tdm->pinctrl)) {
		i2s_tdm->clk_state = pinctrl_lookup_state(i2s_tdm->pinctrl, "clk");
		if (IS_ERR(i2s_tdm->clk_state)) {
			i2s_tdm->clk_state = NULL;
			dev_dbg(i2s_tdm->dev, "Have no clk pinctrl state\n");
		}
	}

#ifdef HAVE_SYNC_RESET
	sync = of_device_is_compatible(node, "rockchip,px30-i2s-tdm") ||
	       of_device_is_compatible(node, "rockchip,rk1808-i2s-tdm") ||
	       of_device_is_compatible(node, "rockchip,rk3308-i2s-tdm");

	if (i2s_tdm->clk_trcm && sync) {
		struct device_node *cru_node;

		cru_node = of_parse_phandle(node, "rockchip,cru", 0);
		i2s_tdm->cru_base = of_iomap(cru_node, 0);
		if (!i2s_tdm->cru_base)
			return -ENOENT;

		i2s_tdm->tx_reset_id = of_i2s_resetid_get(node, "tx-m");
		i2s_tdm->rx_reset_id = of_i2s_resetid_get(node, "rx-m");
	}
#endif

	i2s_tdm->tx_reset = devm_reset_control_get(&pdev->dev, "tx-m");
	if (IS_ERR(i2s_tdm->tx_reset)) {
		ret = PTR_ERR(i2s_tdm->tx_reset);
		if (ret != -ENOENT)
			return ret;
	}

	i2s_tdm->rx_reset = devm_reset_control_get(&pdev->dev, "rx-m");
	if (IS_ERR(i2s_tdm->rx_reset)) {
		ret = PTR_ERR(i2s_tdm->rx_reset);
		if (ret != -ENOENT)
			return ret;
	}

	i2s_tdm->hclk = devm_clk_get(&pdev->dev, "hclk");
	if (IS_ERR(i2s_tdm->hclk))
		return PTR_ERR(i2s_tdm->hclk);

	if (!i2s_tdm->checked_lifecycle) {
		ret = clk_prepare_enable(i2s_tdm->hclk);
		if (ret)
			return ret;
	}

	i2s_tdm->mclk_tx = devm_clk_get(&pdev->dev, "mclk_tx");
	if (IS_ERR(i2s_tdm->mclk_tx))
		return PTR_ERR(i2s_tdm->mclk_tx);

	i2s_tdm->mclk_rx = devm_clk_get(&pdev->dev, "mclk_rx");
	if (IS_ERR(i2s_tdm->mclk_rx))
		return PTR_ERR(i2s_tdm->mclk_rx);

	if (i2s_tdm->checked_lifecycle) {
		/* Reverse devres order: action precedes clk_put for all three handles. */
		ret = devm_add_action_or_reset(&pdev->dev, i2s_checked_probe_clock_release, i2s_tdm);
		if (ret)
			return ret;
		ret = clk_prepare_enable(i2s_tdm->hclk);
		if (ret)
			return ret;
		i2s_tdm->hclk_enabled = true;
	}

	i2s_tdm->io_multiplex =
		of_property_read_bool(node, "rockchip,io-multiplex");

	i2s_tdm->mclk_calibrate =
		of_property_read_bool(node, "rockchip,mclk-calibrate");
	if (i2s_tdm->mclk_calibrate) {
		i2s_tdm->mclk_tx_src = devm_clk_get(&pdev->dev, "mclk_tx_src");
		if (IS_ERR(i2s_tdm->mclk_tx_src))
			return PTR_ERR(i2s_tdm->mclk_tx_src);

		i2s_tdm->mclk_rx_src = devm_clk_get(&pdev->dev, "mclk_rx_src");
		if (IS_ERR(i2s_tdm->mclk_rx_src))
			return PTR_ERR(i2s_tdm->mclk_rx_src);

		i2s_tdm->mclk_root0 = devm_clk_get(&pdev->dev, "mclk_root0");
		if (IS_ERR(i2s_tdm->mclk_root0))
			return PTR_ERR(i2s_tdm->mclk_root0);

		i2s_tdm->mclk_root1 = devm_clk_get(&pdev->dev, "mclk_root1");
		if (IS_ERR(i2s_tdm->mclk_root1))
			return PTR_ERR(i2s_tdm->mclk_root1);

		i2s_tdm->mclk_root0_initial_freq = clk_get_rate(i2s_tdm->mclk_root0);
		i2s_tdm->mclk_root1_initial_freq = clk_get_rate(i2s_tdm->mclk_root1);
		i2s_tdm->mclk_root0_freq = i2s_tdm->mclk_root0_initial_freq;
		i2s_tdm->mclk_root1_freq = i2s_tdm->mclk_root1_initial_freq;
	}

	regs = devm_platform_get_and_ioremap_resource(pdev, 0, &res);
	if (IS_ERR(regs))
		return PTR_ERR(regs);

	i2s_tdm->regs = regs;
	i2s_tdm->regmap = devm_regmap_init_mmio(&pdev->dev, regs,
						&rockchip_i2s_tdm_regmap_config);
	if (IS_ERR(i2s_tdm->regmap))
		return PTR_ERR(i2s_tdm->regmap);

	irq = platform_get_irq_optional(pdev, 0);
	if (i2s_tdm->checked_lifecycle && irq <= 0)
		return irq ? irq : -ENXIO;
	if (irq > 0) {
		i2s_tdm->irq = irq;
		ret = devm_request_irq(&pdev->dev, irq, rockchip_i2s_tdm_isr,
				       IRQF_SHARED, node->name, i2s_tdm);
		if (ret) {
			dev_err(&pdev->dev, "failed to request irq %u\n", irq);
			return ret;
		}
	}

	i2s_tdm->playback_dma_data.addr = res->start + I2S_TXDR;
	i2s_tdm->playback_dma_data.addr_width = DMA_SLAVE_BUSWIDTH_4_BYTES;
	i2s_tdm->playback_dma_data.maxburst = MAXBURST_PER_FIFO;

	i2s_tdm->capture_dma_data.addr = res->start + I2S_RXDR;
	i2s_tdm->capture_dma_data.addr_width = DMA_SLAVE_BUSWIDTH_4_BYTES;
	i2s_tdm->capture_dma_data.maxburst = MAXBURST_PER_FIFO;

	ret = rockchip_i2s_tdm_tx_path_prepare(i2s_tdm, node);
	if (ret < 0) {
		dev_err(&pdev->dev, "I2S TX path prepare failed: %d\n", ret);
		return ret;
	}

	ret = rockchip_i2s_tdm_rx_path_prepare(i2s_tdm, node);
	if (ret < 0) {
		dev_err(&pdev->dev, "I2S RX path prepare failed: %d\n", ret);
		return ret;
	}

	atomic_set(&i2s_tdm->refcount, 0);
	dev_set_drvdata(&pdev->dev, i2s_tdm);

	if (i2s_tdm->checked_lifecycle) {
		ret = i2s_checked_initial_config(i2s_tdm, res->start);
		if (ret)
			return ret;
	} else {
	regmap_update_bits(i2s_tdm->regmap, I2S_DMACR, I2S_DMACR_TDL_MASK,
			   I2S_DMACR_TDL(16));
	regmap_update_bits(i2s_tdm->regmap, I2S_DMACR, I2S_DMACR_RDL_MASK,
			   I2S_DMACR_RDL(16));
	regmap_update_bits(i2s_tdm->regmap, I2S_CKR,
			   I2S_CKR_TRCM_MASK, i2s_tdm->clk_trcm);

	if (i2s_tdm->soc_data && i2s_tdm->soc_data->init)
		i2s_tdm->soc_data->init(&pdev->dev, res->start);
	}

	/*
	 * CLK_ALWAYS_ON should be placed after all registers write done,
	 * because this situation will enable XFER bit which will make
	 * some registers(depend on XFER) write failed.
	 */
	if (i2s_tdm->quirks & QUIRK_ALWAYS_ON) {
		ret = rockchip_i2s_tdm_keep_clk_always_on(i2s_tdm);
		if (ret)
			return ret;
	}

	/*
	 * MUST: after pm_runtime_enable step, any register R/W
	 * should be wrapped with pm_runtime_get_sync/put.
	 *
	 * Another approach is to enable the regcache true to
	 * avoid access HW registers.
	 *
	 * Alternatively, performing the registers R/W before
	 * pm_runtime_enable is also a good option.
	 */
	pm_runtime_enable(&pdev->dev);
	if (i2s_tdm->checked_lifecycle)
		i2s_tdm->pm_enabled = true;
	if (!pm_runtime_enabled(&pdev->dev)) {
		ret = i2s_tdm_runtime_resume(&pdev->dev);
		if (ret) {
			if (i2s_tdm->checked_lifecycle)
				goto err_suspend;
			goto err_pm_disable;
		}
	}

	ret = devm_snd_soc_register_component(&pdev->dev,
					      i2s_tdm->checked_lifecycle ?
					      &rockchip_i2s_tdm_checked_component :
					      &rockchip_i2s_tdm_component,
					      soc_dai, 1);

	if (ret) {
		dev_err(&pdev->dev, "Could not register DAI\n");
		goto err_suspend;
	}

	if (of_property_read_bool(node, "rockchip,no-dmaengine")) {
		dev_info(&pdev->dev, "Used for Multi-DAI\n");
		return 0;
	}

	if (of_property_read_bool(node, "rockchip,digital-loopback"))
		ret = devm_snd_dmaengine_dlp_register(&pdev->dev, &dconfig);
	else
		ret = devm_snd_dmaengine_pcm_register(&pdev->dev, NULL, 0);

	if (ret) {
		dev_err(&pdev->dev, "Could not register PCM\n");
		if (i2s_tdm->checked_lifecycle)
			goto err_suspend;
		return ret;
	}

	if (i2s_tdm->checked_lifecycle) {
		ret = devm_device_add_group(&pdev->dev, &i2s_checked_group);
		if (ret)
			goto err_suspend;
		WRITE_ONCE(i2s_tdm->ready, true);
	}
	return 0;

err_suspend:
	if (i2s_tdm->checked_lifecycle) {
		/* Preserve registration errno; prove exit before any devres release. */
		i2s_checked_quiesce(i2s_tdm, "probe late failure");
		return ret;
	}
	if (!pm_runtime_status_suspended(&pdev->dev))
		i2s_tdm_runtime_suspend(&pdev->dev);
err_pm_disable:
	pm_runtime_disable(&pdev->dev);

	return ret;
}

static int rockchip_i2s_tdm_remove(struct platform_device *pdev)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(&pdev->dev);

	if (i2s_tdm->checked_lifecycle) {
		i2s_checked_quiesce(i2s_tdm, "remove");
		return 0;
	}

	pm_runtime_disable(&pdev->dev);
	if (!pm_runtime_status_suspended(&pdev->dev))
		i2s_tdm_runtime_suspend(&pdev->dev);

	clk_disable_unprepare(i2s_tdm->mclk_tx);
	clk_disable_unprepare(i2s_tdm->mclk_rx);
	clk_disable_unprepare(i2s_tdm->hclk);

	return 0;
}

static void rockchip_i2s_tdm_platform_shutdown(struct platform_device *pdev)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(&pdev->dev);

	if (i2s_tdm->checked_lifecycle) {
		i2s_checked_quiesce(i2s_tdm, "platform shutdown");
		return;
	}

	pm_runtime_get_sync(i2s_tdm->dev);
	rockchip_i2s_tdm_stop(i2s_tdm, SNDRV_PCM_STREAM_PLAYBACK);
	rockchip_i2s_tdm_stop(i2s_tdm, SNDRV_PCM_STREAM_CAPTURE);
	pm_runtime_put(i2s_tdm->dev);
}

#ifdef CONFIG_PM_SLEEP
static int rockchip_i2s_tdm_suspend(struct device *dev)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);

	if (i2s_tdm->checked_lifecycle)
		return pm_runtime_force_suspend(dev);

	regcache_mark_dirty(i2s_tdm->regmap);

	return 0;
}

static int rockchip_i2s_tdm_resume(struct device *dev)
{
	struct rk_i2s_tdm_dev *i2s_tdm = dev_get_drvdata(dev);
	int ret;

	if (i2s_tdm->checked_lifecycle)
		return pm_runtime_force_resume(dev);

	ret = pm_runtime_get_sync(dev);
	if (ret < 0)
		return ret;
	ret = regcache_sync(i2s_tdm->regmap);
	pm_runtime_put(dev);

	return ret;
}
#endif

static const struct dev_pm_ops rockchip_i2s_tdm_pm_ops = {
	SET_RUNTIME_PM_OPS(i2s_tdm_runtime_suspend, i2s_tdm_runtime_resume,
			   NULL)
	SET_SYSTEM_SLEEP_PM_OPS(rockchip_i2s_tdm_suspend,
				rockchip_i2s_tdm_resume)
};

static struct platform_driver rockchip_i2s_tdm_driver = {
	.probe = rockchip_i2s_tdm_probe,
	.remove = rockchip_i2s_tdm_remove,
	.shutdown = rockchip_i2s_tdm_platform_shutdown,
	.driver = {
		.name = DRV_NAME,
		.of_match_table = of_match_ptr(rockchip_i2s_tdm_match),
		.pm = &rockchip_i2s_tdm_pm_ops,
	},
};
module_platform_driver(rockchip_i2s_tdm_driver);

MODULE_DESCRIPTION("ROCKCHIP I2S/TDM ASoC Interface");
MODULE_AUTHOR("Sugar Zhang <sugar.zhang@rock-chips.com>");
MODULE_LICENSE("GPL v2");
MODULE_ALIAS("platform:" DRV_NAME);
MODULE_DEVICE_TABLE(of, rockchip_i2s_tdm_match);

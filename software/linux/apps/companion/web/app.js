"use strict";
(() => {
    const $ = id => document.getElementById(id);
    const labels = {
        offline: ["等待连接", "连接语音服务后，可开启设备麦克风。"],
        connecting: ["正在连接", "正在与语音服务建立连接，请稍候。"],
        idle: ["我在这里", "按住说话，松开后我会回答你。"],
        listening: ["我在听", "正在采集设备麦克风，松开按钮结束。"],
        thinking: ["想一想", "录音已结束，正在等待语音服务回复。"],
        speaking: ["正在回答", "你可以停止回答，再开始下一次对话。"],
        error: ["需要处理", "请查看下方提示，检查设备和服务配置。"],
        muted: ["麦克风已静音", "开启麦克风后，按住按钮才会录音。"]
    };
    const emotions = {neutral:"平静",happy:"开心",sad:"低落",angry:"认真",surprised:"惊喜",thinking:"思考",calm:"平静"};
    let authorized = false;
    let page = "home";
    let cameraSelected = false;
    let access = {};
    let logLoading = false;
    let logRevision = 0;
    const deviceOwned = value => (value.control_mode || (value.capabilities || {}).control_mode) === "device";
    let current = {connected:false, muted:true, state:"offline"};
    let reachable = false;
    let held = false;
    let pending = 0;
    let actionQueue = Promise.resolve();
    let actionError = "";
    let statusRevision = 0;
    let lastLease = 0;
    let displayedError = "";
    function showError(message) {
        const value = String(message || "");
        if (value !== displayedError) {
            $("error").textContent = value;
            displayedError = value;
        }
        $("error").hidden = !value;
    }
    async function request(url, options = {}, timeout = 5000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeout);
        try {
            const response = await fetch(url, {...options, signal:controller.signal, cache:"no-store"});
            const body = await response.json();
            if (response.status === 401 && !["/api/access", "/api/pair"].includes(url)) {
                authorized = false;
                showAccessGate("访问授权已过期，请重新配对。", true);
            }
            if (!response.ok || body.ok === false) {
                throw new Error(typeof body.error === "string" ? body.error : "设备请求失败（" + response.status + "）");
            }
            return body;
        } finally { clearTimeout(timer); }
    }
    function render() {
        const micOn = reachable && !current.muted && (current.continuous || current.state === "listening");
        $("mic-status").dataset.active = reachable ? String(micOn) : "unknown";
        $("mic-status").textContent = !reachable ? "麦克风状态未知" : micOn ? "● 麦克风 ON · 正在采集" : current.muted ? "麦克风 OFF · 已关闭" : "麦克风待命 · 未采集";
        const state = !reachable ? "offline" : (current.muted && current.state === "idle" ? "muted" : current.state);
        let [label, hint] = labels[state] || ["等待状态", "正在同步设备状态。"];
        const demo = current.capabilities && current.capabilities.mode === "demo";
        const streaming = !!(current.capabilities && current.capabilities.streaming_asr);
        const fullDuplex = !demo && streaming && !!(current.capabilities && current.capabilities.full_duplex);
        const inputActive = fullDuplex && reachable && current.connected && current.continuous && !current.muted && ["waiting_speech", "recognizing"].includes(current.input_state);
        const local = !demo && current.capabilities && current.capabilities.voice_backend === "local";
        $("mode-banner").hidden = !demo && !local && reachable;
        $("mode-banner").textContent = demo ? "演示模式 · 模拟对话流程，不采集麦克风、不播放真实声音、不连接语音后端。" : local ? "本地识别与合成 · 千帆文字回答" : "正在获取设备运行模式…";
        $("privacy-note").textContent = demo ? "当前操作仅演示交互。真实语音需要配置后端与设备声卡，并切换运行模式。" : local ? "音频只在设备本地处理，识别后的文字会发送到千帆生成回答。松开结束录音；静音将停止采集。" : "音频在按住说话期间发送到已配置的后端。松开结束录音；静音将停止采集。";
        if (!current.capabilities) $("privacy-note").textContent = "正在确认语音处理方式。麦克风默认静音，开启后按住按钮才会录音。";
        $("input-hint").textContent = demo ? "模拟交互 · 空格 / 回车也可按住体验" : "使用设备麦克风 · 空格 / 回车也可按住说话";
        if (demo) {
            const demoHints = {offline:"点击开始演示，体验设备的对话流程。", muted:"点击启用演示交互，再按住按钮体验。", idle:"按住按钮，松开后查看示例回答。", listening:"正在演示聆听状态，未采集声音；松开查看示例回答。", thinking:"正在准备预设示例回答，未调用真实后端。", speaking:"正在展示预设回答，未播放真实声音。"};
            hint = demoHints[state] || hint;
        }
        if (local) {
            const localLabels = {offline:["等待准备", "点击准备语音，加载本地识别与合成模型。"], connecting:["正在准备", "正在加载本地语音模型，请稍候。"], listening:["我在听", "正在采集设备麦克风，松开后在本地识别。"]};
            [label, hint] = localLabels[state] || [label, hint];
        }
        if (!demo && ["thinking", "speaking"].includes(state)) {
            const progressLabels = {
                waiting_recognition:["等待识别", "录音已结束，正在等待服务识别你说的话。"],
                waiting_reply:["等待回答", "已识别你的话，正在等待回答。"],
                waiting_audio:["等待语音", "服务正在准备语音回复，设备尚未收到声音数据。"],
                receiving_audio:["收到语音回复", "语音已送往设备播放；如没有声音，请检查音量和扬声器。"],
                complete:["本轮已完成", "这一轮对话已结束，稍候可再次按住说话。"],
                failed:["本轮未完成", "请查看错误提示，处理后再重试。"]
            };
            if (local) {
                progressLabels.waiting_recognition = ["本地识别中", "录音已结束，正在设备上识别你说的话。音频不会上传。"];
                progressLabels.waiting_reply = ["等待千帆回答", "已将识别文字发送到千帆，正在等待回答。音频保留在本地处理。"];
                progressLabels.waiting_audio = ["本地合成中", "正在设备上合成声音，准备就绪后由扬声器播放。"];
                progressLabels.receiving_audio = ["正在播放回答", "本地合成的语音已送往设备播放；如没有声音，请检查音量和扬声器。"];
            }
            [label, hint] = progressLabels[current.voice_progress] || (state === "speaking" ? progressLabels.waiting_audio : progressLabels.waiting_recognition);
        }
        if (streaming) {
            $("privacy-note").textContent = fullDuplex ? "音频只在设备本地识别。连续对话期间，等待回答和播放时也会保持采集与识别；新的一句排队，当前回答播放结束后再发送到千帆并回应。不会自动打断。静音、离开页面或控制台失联后停止采集。" : "音频只在设备本地识别，完成的一句话才会发送到千帆。连续对话会在回答播放结束后恢复识别；静音、离开页面或控制台失联后会停止。";
            $("input-hint").textContent = current.continuous ? (fullDuplex ? "连续对话中 · 边说边听 · 新的一句排队回应" : "连续对话中 · 停顿后自动提交 · 回答时暂停识别") : "可按住说话，或点击连续对话自动断句";
            if (state === "muted") hint = "开启麦克风后，选择按住说话或连续对话。";
            if (state === "idle") hint = "按住说话，或点击开始连续对话，停顿后自动提交。";
            if (state === "listening") {
                if (current.continuous && current.voice_progress === "waiting_speech") {
                    label = "等待说话";
                    hint = "正在进行轻量声音检测，尚未运行语音识别。说话后开始识别。";
                } else {
                    hint = current.continuous ? "边说边出字，停顿约 1～2 秒后自动提交。" : "正在实时识别，松开按钮提交这一句。";
                }
            }
            if (current.voice_progress === "no_speech" && state === "idle") hint = "没有识别到话音，未发送云端。";
        }
        if (deviceOwned(current) && streaming) {
            $("privacy-note").textContent = "音频在设备本地处理，识别后的文字发送到千帆。连续对话由设备管理，切换页面或关闭浏览器不会停止；请使用“立即静音”关闭麦克风。刷新页面不会自动开启麦克风。";
        }
        const queued = Number.isInteger(current.pending_utterances) ? Math.max(0, Math.min(3, current.pending_utterances)) : 0;
        $("duplex-input").hidden = !fullDuplex || !current.continuous;
        $("duplex-input").dataset.active = String(!!inputActive);
        $("duplex-state").textContent = !reachable ? "连接中断 · 正在等待状态" : inputActive ? (current.input_state === "recognizing" ? "正在听你说 · 识别中" : "正在听 · 等待新的一句") : "采集已停止";
        $("pending-count").textContent = queued ? "待回应 " + queued + " / 3" : "没有排队的话";
        $("next-transcript").textContent = typeof current.next_transcript === "string" && current.next_transcript ? current.next_transcript : (inputActive ? "你可以继续说，听到的新一句会显示在这里。" : "开始连续对话后，等待回答和播放时也能听你说。");
        $("duplex-note").textContent = queued >= 3 ? "已有 3 句等待回应，请等队列减少后再说。当前回答不会自动中断。" : "下一句会等当前回答播放结束后再回应；立即静音可停止所有采集。";
        const echoSuspect = typeof current.echo_suspect === "string" ? current.echo_suspect : "";
        const showEcho = fullDuplex && current.continuous && !!echoSuspect;
        $("echo-review").hidden = !showEcho;
        $("echo-text").textContent = echoSuspect;
        const echoDisabled = !showEcho || !reachable || !current.connected || current.muted || ["offline", "error", "connecting"].includes(state) || pending > 0;
        $("confirm-echo").disabled = echoDisabled;
        $("dismiss-echo").disabled = echoDisabled;
        $("echo-note").textContent = inputActive ? "内容与近期播放相似，暂未发送，也未加入待回应队列。麦克风仍在听；如果这是你说的，请确认。" : "内容与近期播放相似，暂未发送，也未加入待回应队列。这只是疑似判断。";
        if (inputActive && ["thinking", "speaking"].includes(state)) {
            if (state === "speaking") label = "边说边听";
            hint += " 你可以继续说，新的一句会排队回应，不会自动打断。";
        }
        $("continuous").hidden = !streaming;
        $("continuous").disabled = !reachable || pending > 0 || !current.connected || current.muted || (!current.continuous && current.state !== "idle");
        $("continuous").textContent = current.continuous ? "结束连续对话" : "开始连续对话";
        $("continuous").setAttribute("aria-pressed", String(!!current.continuous));
        $("transcript-label").textContent = current.transcript_partial ? "你说 · 识别中" : "你说";
        $("avatar").dataset.voiceProgress = demo ? "receiving_audio" : current.voice_progress || (state === "speaking" ? "waiting_audio" : "idle");
        $("avatar").dataset.state = state;
        $("avatar").dataset.emotion = emotions[current.emotion] ? current.emotion : "neutral";
        $("avatar").setAttribute("aria-label", label);
        $("state-label").textContent = label;
        $("state-hint").textContent = hint;
        $("connection").textContent = !reachable ? "控制台连接中断" : current.connected ? (demo ? "演示已就绪" : local ? "本地语音已就绪" : "语音服务已连接") : (demo ? "控制台在线 · 演示未开始" : local ? "控制台在线 · 本地语音未准备" : "控制台在线 · 语音未连接");
        $("connection-dot").classList.toggle("online", reachable && !!current.connected);
        $("connect").textContent = current.connected ? (demo ? "结束演示" : local ? "关闭语音" : "断开语音服务") : (demo ? "开始演示" : local ? "准备语音" : "连接语音服务");
        $("connect").disabled = !reachable || pending > 0 || current.state === "connecting";
        $("mute").textContent = current.muted ? (demo ? "启用演示交互" : "开启麦克风") : (demo ? "暂停演示交互" : "立即静音");
        $("mute").setAttribute("aria-pressed", String(!!current.muted));
        $("mute").disabled = !reachable;
        $("global-mute").disabled = !reachable || current.muted || pending > 0;
        $("talk").disabled = !!current.continuous || !reachable || !current.connected || current.muted || (!held && (pending > 0 || !["idle", "listening"].includes(current.state)));
        $("talk").textContent = held ? (demo ? "模拟聆听 · 松开继续" : "正在听 · 松开结束") : (demo ? "按住体验对话" : "按住说话");
        if (held && ["thinking", "speaking", "error", "offline"].includes(current.state)) $("talk").textContent = "已结束录音 · 请松开";
        $("talk").classList.toggle("held", held && current.state === "listening");
        $("interrupt").disabled = !reachable || !["speaking","thinking","listening"].includes(current.state);
        $("emotion").textContent = emotions[current.emotion] || "平静";
        $("transcript").textContent = current.transcript || "你的话会出现在这里";
        $("reply").textContent = current.reply || (local ? "点击准备语音，待本地模型就绪后开启麦克风。" : "准备好后，连接服务并开启麦克风。");
        const face = current.face || {};
        $("face-state").textContent = face.available ? (face.running ? "运行中" : "已连接 · 未运行") : "未连接";
        const faces = Array.isArray(face.faces) ? face.faces.filter(item => item && typeof item === "object").slice(0, 16) : [];
        $("face-summary").textContent = !face.available ? "人脸服务暂不可用，语音功能可独立使用。" :
            !face.running ? "等待人脸服务启动" : faces.length ? "画面中发现 " + faces.length + " 张人脸" : "画面中暂未检测到人脸";
        const list = document.createDocumentFragment();
        for (const face of faces) {
            const row = document.createElement("li");
            const name = document.createElement("strong");
            name.textContent = !face.name || face.name === "unknown" ? "未登记访客" : String(face.name);
            const score = document.createElement("span");
            score.textContent = Number.isFinite(Number(face.similarity)) ? "相似度 " + Number(face.similarity).toFixed(3) : "";
            row.append(name, score);
            list.append(row);
        }
        $("faces").replaceChildren(list);
        const m = current.metrics || {};
        const finite = value => typeof value === "number" && Number.isFinite(value);
        const metrics = [];
        if (finite(m.memory_used_mb) && finite(m.memory_total_mb)) metrics.push("内存 " + Math.round(m.memory_used_mb) + " / " + Math.round(m.memory_total_mb) + " MB");
        if (finite(m.system_cpu_percent)) metrics.push("整机 CPU " + m.system_cpu_percent.toFixed(1) + "%" + (finite(m.cpu_logical_cores) ? "（" + m.cpu_logical_cores + "核）" : ""));
        metrics.push(finite(m.npu_load_percent) ? "NPU " + m.npu_load_percent.toFixed(0) + "%" : "NPU 暂无读数");
        if (local) {
            const asr = current.capabilities.local_asr_backend === "rknn" ? "NPU + CPU前后处理" : "CPU";
            const tts = current.capabilities.local_tts_backend === "rknn" ? "Melo：NPU波形生成 + CPU前端" : "CPU";
            metrics.push("ASR：" + asr + " · TTS：" + tts);
        }
        if (finite(m.rss_mb)) metrics.push("伴随服务 " + m.rss_mb.toFixed(1) + " MB");
        if (finite(m.local_speech_rss_mb)) metrics.push("语音主进程 " + m.local_speech_rss_mb.toFixed(1) + " MB");
        if (finite(m.local_speech_cpu_percent)) metrics.push("语音主进程 CPU " + m.local_speech_cpu_percent.toFixed(1) + "%（单核100%）");
        if (finite(m.local_speech_peak_rss_mb)) metrics.push("语音主进程峰值 " + m.local_speech_peak_rss_mb.toFixed(1) + " MB");
        if (finite(m.local_speech_threads)) metrics.push("语音线程 " + m.local_speech_threads);
        if (local && current.capabilities.local_asr_backend === "rknn") metrics.push("主进程统计不含独立NPU识别进程");
        if (local && m.local_speech_running === false) metrics.push("本地模型进程未运行");
        if (finite(m.temperature_c)) metrics.push("温度 " + m.temperature_c.toFixed(1) + "℃");
        if (finite(m.cpu_percent)) metrics.push("伴随服务 CPU " + m.cpu_percent.toFixed(1) + "%");
        if (face.available && finite(face.fps)) metrics.push("识别 " + face.fps.toFixed(1) + " FPS");
        $("metrics").textContent = metrics.join(" · ") || "暂未提供资源数据";
        if (finite(m.capture_peak_amplitude)) {
            const peak = m.capture_peak_amplitude;
            $("metrics").textContent += " · 本轮录音峰值 " + peak + " / 32768" +
                (peak > 0 ? "（" + (20 * Math.log10(peak / 32768)).toFixed(1) + " dBFS，" + (peak / 32768 * 100).toFixed(1) + "%）" : "（本轮尚无有效电平）");
        }
        const timing = current.latency_ms && Object.keys(current.latency_ms).length ? current.latency_ms : current.last_latency_ms || {};
        const stages = [["asr_first_partial_ms", "识别首字"], ["asr_finalize_ms", "识别收尾"], ["cloud_first_token_ms", "千帆首字"], ["cloud_first_sentence_ms", "千帆首句"], ["cloud_ms", "千帆全部"],
            ["tts_first_pcm_ms", "首个音频"], ["tts_first_audio_ms", "起播缓冲就绪"], ["tts_total_ms", "合成全部"],
            ["playback_queue_ms", "播放提交"], ["stop_to_audio_ms", "停录至播放提交"]];
        $("latency").textContent = stages.filter(([key]) => finite(timing[key])).map(([key, title]) =>
            title + " " + (timing[key] / 1000).toFixed(2) + "s").join(" · ");
        const c = current.capabilities || {};
        const capabilities = [];
        if (c.audio_input === false || c.recording === false) capabilities.push("录音未就绪");
        if (c.playback === false || c.audio_output === false) capabilities.push("播放未就绪");
        if (c.wake_word === false) capabilities.push("唤醒词未启用");
        if (c.aec) capabilities.push("实验性回声消除已开启；对话期间回答时也保持本地麦克风采集，关闭麦克风即停止");
        if (fullDuplex) capabilities.push("连续对话支持边说边听，新一句排队回应；不支持自动打断");
        else if (c.aec === false || c.automatic_barge_in === false) capabilities.push((streaming ? "流式识别、停顿断句；" : "按住说话；") + "回答时请先点击停止，不支持直接说话打断");
        $("capabilities").textContent = capabilities.join(" · ");
        showError(actionError || (typeof current.error === "string" ? current.error : ""));
    }
    function action(name) {
        pending++;
        statusRevision++;
        render();
        actionQueue = actionQueue.then(async () => {
            try {
                const next = await request("/api/action", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:name})}, 15000);
                if (next && typeof next.state === "string") { current = next; reachable = true; }
                actionError = "";
            } catch (error) {
                actionError = error.name === "AbortError" ? "操作超时，请检查设备连接。" : error.message;
            } finally {
                pending--;
                statusRevision++;
                render();
            }
        });
        return actionQueue;
    }
    function startTalk(event) {
        if (held || $("talk").disabled) return;
        if (event.type === "pointerdown" && event.button !== 0) return;
        event.preventDefault();
        held = true;
        if (event.pointerId !== undefined) $("talk").setPointerCapture(event.pointerId);
        action("listen");
    }
    function stopTalk() {
        if (!held) return;
        held = false;
        action("stop");
    }
    $("confirm-echo").addEventListener("click", () => { if (!$("confirm-echo").disabled) action("confirm_echo:" + current.echo_suspect_id); });
    $("dismiss-echo").addEventListener("click", () => { if (!$("dismiss-echo").disabled) action("dismiss_echo:" + current.echo_suspect_id); });
    $("continuous").addEventListener("click", () => { stopTalk(); action(current.continuous ? "mute" : "continuous"); });
    $("connect").addEventListener("click", () => { stopTalk(); action(current.connected ? "disconnect" : "connect"); });
    $("global-mute").addEventListener("click", () => { stopTalk(); action("mute"); });
    $("mute").addEventListener("click", () => { stopTalk(); action(current.muted ? "unmute" : "mute"); });
    $("interrupt").addEventListener("click", () => { stopTalk(); action("interrupt"); });
    $("talk").addEventListener("pointerdown", startTalk);
    $("talk").addEventListener("pointerup", stopTalk);
    $("talk").addEventListener("pointercancel", stopTalk);
    $("talk").addEventListener("lostpointercapture", stopTalk);
    $("talk").addEventListener("contextmenu", event => event.preventDefault());
    $("talk").addEventListener("keydown", event => {
        if ([" ", "Enter"].includes(event.key)) {
            event.preventDefault();
            if (!event.repeat) startTalk(event);
        }
    });
    $("talk").addEventListener("keyup", event => {
        if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); stopTalk(); }
    });
    $("talk").addEventListener("blur", stopTalk);
    function leaveConversation() { if (deviceOwned(current)) return; stopTalk(); if (current.continuous) action("mute"); }
    // Losing focus to the chat pane is not leaving the visible conversation.
    window.addEventListener("blur", () => { if (!deviceOwned(current)) stopTalk(); });
    document.addEventListener("visibilitychange", () => { updateCamera(); if (document.hidden) leaveConversation(); });
    window.addEventListener("pagehide", () => {
        $("camera-image").removeAttribute("src");
        if (deviceOwned(current)) return;
        if (held || current.continuous) {
            const stopAction = current.continuous ? "mute" : "stop";
            held = false;
            // Server-side recording timeout remains the fallback if this request is lost.
            fetch("/api/action", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:stopAction}), keepalive:true}).catch(() => {});
        }
    });
    async function poll() {
        if (!authorized) { setTimeout(poll, 1000); return; }
        const revision = statusRevision;
        try {
            const next = await request("/api/status");
            if (!deviceOwned(next) && next.continuous && !document.hidden && Date.now() - lastLease > 4000) {
                await request("/api/action", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:"keep_listening"})});
                lastLease = Date.now();
            }
            if (revision === statusRevision && pending === 0) {
                current = next;
                reachable = true;
                $("updated").textContent = "刚刚同步";
                render();
            }
        } catch (error) {
            reachable = false;
            stopTalk();
            $("updated").textContent = "同步中断";
            render();
            showError("无法连接设备中心，正在自动重试。");
        } finally {
            setTimeout(poll, current.state === "listening" ? 250 : 1000);
        }
    }
    // Wi-Fi is managed independently from voice. Opening this panel only reads status.
    let network = {available:false, busy:false, networks:[]};
    let networkLoading = false;
    let networkSubmitting = false;
    let networkTimer = null;
    let networkRevision = 0;
    let networkMessage = "";
    const networkPanel = $("network-panel");
    function selectedNetwork() {
        return network.networks.find(item => item.service === $("network-select").value);
    }
    function renderNetwork() {
        const selected = $("network-select").value;
        const list = document.createDocumentFragment();
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = network.networks.length ? "请选择网络" : "暂无网络，请点击扫描 Wi-Fi";
        list.append(placeholder);
        for (const item of network.networks) {
            const option = document.createElement("option");
            option.value = item.service;
            option.textContent = item.ssid + (item.connected && !network.stale && network.service_available !== false && network.service_available !== null ? " · 已连接" : "") + (item.security === "none" || item.security === "open" ? " · 开放网络" : "");
            list.append(option);
        }
        $("network-select").replaceChildren(list);
        $("network-select").value = network.networks.some(item => item.service === selected) ? selected : "";
        const item = selectedNetwork();
        const supported = item && !item.hidden && ["psk", "open", "none"].includes(item.security);
        const busy = network.busy || networkSubmitting;
        const unavailable = !network.available || network.service_available === false;
        const stateUncertain = !!network.stale || network.service_available === null;
        $("network-enable").disabled = unavailable || busy;
        $("network-scan").disabled = unavailable || busy;
        $("network-select").disabled = unavailable || busy || !network.networks.length;
        $("network-password").disabled = unavailable || busy || stateUncertain || !item || !supported || item.connected || item.security === "none" || item.security === "open";
        $("network-connect").disabled = unavailable || busy || stateUncertain || !item || item.connected || !supported;
        $("network-disconnect").disabled = unavailable || busy || stateUncertain || !item || !item.connected;
        $("network-count").textContent = network.available ? network.networks.length + " 个网络" : "";
        const statuses = {idle:"网络管理已就绪。点击扫描发现附近的 Wi-Fi。", enabling:"正在开启 Wi-Fi，设备可能重连已保存的网络…", scanning:"正在扫描附近的 Wi-Fi…", connecting:"正在连接所选网络…", disconnecting:"正在断开所选网络…", error:"上次网络操作失败，请查看提示后重试。"};
        const status = statuses[network.status] || "";
        $("network-status").textContent = !network.available ? "当前设备未启用 Wi-Fi 管理，网线可继续使用。" : network.service_available === false ? "设备的 Wi-Fi 管理服务暂不可用，网线可继续使用。" : busy ? (status && network.status !== "idle" ? status : "正在处理网络操作，请稍候…") : item && !supported ? "此网络类型暂不支持连接，可选择普通密码网络或开放网络。" : status || "网络管理已就绪。点击扫描发现附近的 Wi-Fi。";
        const refreshError = typeof network.refresh_error === "string" ? network.refresh_error : "";
        const refresh = network.stale ? "网络状态已过期，不能确认是否已连接。刷新完成前，连接和断开暂不可用。" : network.refreshing ? "正在后台刷新网络状态，当前网络操作仍可使用。" : network.service_available === null ? "正在确认设备网络服务是否可用…" : "";
        $("network-refresh").textContent = [refresh, refreshError].filter(Boolean).join(" ");
        $("network-refresh").hidden = !refresh && !refreshError;
        const address = item && item.connected && !network.stale && !unavailable && network.service_available !== null && typeof item.ipv4 === "string" ? item.ipv4 : "";
        $("network-address").textContent = address ? "所选 Wi-Fi 已连接 · IPv4：" + address : "";
        $("network-address").hidden = !address;
        const error = networkMessage || (typeof network.error === "string" ? network.error : "");
        $("network-error").textContent = error;
        $("network-error").hidden = !error;
    }
    function acceptNetwork(value) {
        if (!value || typeof value !== "object") throw new Error("invalid network status");
        network = {...value, networks:Array.isArray(value.networks) ? value.networks.filter(item => item && typeof item.service === "string" && typeof item.ssid === "string").slice(0,128) : []};
        renderNetwork();
    }
    function scheduleNetwork() {
        if (networkTimer !== null) clearTimeout(networkTimer);
        networkTimer = null;
        if (authorized && page === "settings" && networkPanel.open && !document.hidden) networkTimer = setTimeout(pollNetwork, 3000);
    }
    async function pollNetwork() {
        if (!authorized || page !== "settings" || !networkPanel.open || document.hidden || networkLoading || networkSubmitting) return;
        networkLoading = true;
        const revision = networkRevision;
        try {
            const next = await request("/api/network");
            if (revision === networkRevision) { networkMessage = ""; acceptNetwork(next); }
        } catch (_) {
            if (revision === networkRevision) {
                networkMessage = "无法读取网络状态，展开此区域时会自动重试。网线可继续使用。";
                network.stale = true;
                renderNetwork();
            }
        } finally {
            networkLoading = false;
            scheduleNetwork();
        }
    }
    async function networkAction(action) {
        if (networkSubmitting || network.busy || !network.available || network.service_available === false) return;
        if (["connect", "disconnect"].includes(action) && (network.stale || network.service_available === null)) return;
        const item = selectedNetwork();
        if (!["scan", "enable"].includes(action) && !item) return;
        if (action === "connect" && (item.hidden || !["psk", "open", "none"].includes(item.security))) return;
        const body = {action};
        if (item && !["scan", "enable"].includes(action)) body.service = item.service;
        if (action === "connect") body.password = $("network-password").disabled ? "" : $("network-password").value;
        // Clear immediately, including failed submissions; never persist credentials.
        $("network-password").value = "";
        networkSubmitting = true;
        networkRevision++;
        networkMessage = "";
        renderNetwork();
        try {
            const next = await request("/api/network", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}, 15000);
            if (typeof next.available === "boolean") acceptNetwork(next);
            else network.busy = true;
        } catch (_) {
            networkMessage = "网络操作未完成，请查看设备状态后重试。若正在切换 Wi-Fi，控制台可能暂时断开。";
        } finally {
            delete body.password;
            networkSubmitting = false;
            networkRevision++;
            renderNetwork();
            scheduleNetwork();
        }
    }
    $("network-enable").addEventListener("click", () => networkAction("enable"));
    $("network-scan").addEventListener("click", () => networkAction("scan"));
    $("network-form").addEventListener("submit", event => { event.preventDefault(); networkAction("connect"); });
    $("network-disconnect").addEventListener("click", () => networkAction("disconnect"));
    $("network-select").addEventListener("change", () => { $("network-password").value = ""; renderNetwork(); });
    networkPanel.addEventListener("toggle", () => {
        if (authorized && page === "settings" && networkPanel.open && !document.hidden) pollNetwork();
        else { $("network-password").value = ""; scheduleNetwork(); }
    });
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && networkPanel.open) pollNetwork();
        else scheduleNetwork();
    });
    // Device settings are read on expansion; edits require an explicit application.
    const devicePanel = $("device-panel");
    const deviceFields = {
        volume:{key:"volume_percent", unit:"%", min:0, max:100},
        speaker:{key:"speaker_enabled"},
        mic_gain:{key:"mic_gain_db", unit:" dB", values:[0,6,12,18,24,30,36,42]},
        brightness:{key:"brightness_percent", unit:"%", min:10, max:100}
    };
    let device = {available:false, supported:{}};
    let deviceLoading = false, deviceSubmitting = false, deviceTimer = null;
    let deviceRevision = 0, deviceMessage = "";
    const deviceDirty = new Set();
    function validDeviceValue(name, value) {
        const field = deviceFields[name];
        return name === "speaker" ? typeof value === "boolean" : Number.isInteger(value) &&
            (field.values ? field.values.includes(value) : value >= field.min && value <= field.max);
    }
    function renderDevice() {
        const usable = device.available && !device.error && !deviceMessage;
        const busy = deviceSubmitting || device.busy;
        $("device-status").textContent = deviceSubmitting ? "正在应用设置并读取设备实际值…" :
            !usable ? "设备设置暂不可用，展开此区域时会自动重试。" : device.busy ? "设备正在处理设置，请稍候。" : "已读取设备设置；修改后点击对应的应用按钮。";
        $("device-error").textContent = deviceMessage || device.error || "";
        $("device-error").hidden = !$("device-error").textContent;
        $("device-diagnostic").textContent = typeof device.diagnostic === "string" ? device.diagnostic : "";
        for (const [name, field] of Object.entries(deviceFields)) {
            const input = $("device-" + name);
            const value = device[field.key];
            const readable = name === "mic_gain" ? typeof value === "number" && Number.isFinite(value) :
                name === "brightness" ? Number.isInteger(value) && value >= 0 && value <= 100 : validDeviceValue(name, value);
            const known = usable && device.supported[name] === true && readable;
            input.disabled = !known || busy;
            $("device-" + name + "-apply").disabled = !known || busy || !deviceDirty.has(name);
            $("device-" + name + "-current").textContent = known ? (device.busy ? "上次读取：" : "设备当前：") +
                (name === "speaker" ? (value ? "已打开" : "已关闭") : value + field.unit) :
                usable && device.supported[name] === false ? "此设备暂不支持" : "状态未知";
            if (!known) {
                deviceDirty.delete(name);
                input.value = "";
            } else if (!deviceDirty.has(name) && document.activeElement !== input) {
                input.value = name === "mic_gain" && !field.values.includes(value) ? "" :
                    name === "brightness" ? String(Math.max(field.min, value)) : String(value);
            }
            if (known && name === "brightness" && value < field.min) {
                deviceDirty.add(name);
                $("device-" + name + "-apply").disabled = !!busy;
            }
            if (name === "volume" || name === "brightness") {
                $("device-" + name + "-value").textContent = known ? input.value + "%" : "—";
                input.setAttribute("aria-valuetext", known ? input.value + "%" : "状态未知");
            }
        }
    }
    function acceptDevice(next) {
        if (!next || typeof next.available !== "boolean" || !next.supported || typeof next.supported !== "object") throw new Error("设备设置响应无效");
        device = next;
        renderDevice();
    }
    function scheduleDevice() {
        if (deviceTimer !== null) clearTimeout(deviceTimer);
        deviceTimer = null;
        if (authorized && page === "settings" && devicePanel.open && !document.hidden) deviceTimer = setTimeout(pollDevice, 5000);
    }
    async function pollDevice() {
        if (!authorized || page !== "settings" || !devicePanel.open || document.hidden || deviceLoading || deviceSubmitting) return;
        deviceLoading = true;
        const revision = deviceRevision;
        try {
            const next = await request("/api/device");
            if (revision === deviceRevision) { deviceMessage = ""; acceptDevice(next); }
        } catch (_) {
            if (revision === deviceRevision) {
                device = {available:false, supported:{}};
                deviceMessage = "无法读取设备设置，恢复连接后再试。";
                renderDevice();
            }
        } finally { deviceLoading = false; scheduleDevice(); }
    }
    async function applyDevice(name) {
        const input = $("device-" + name);
        if (input.disabled || !deviceDirty.has(name) || deviceSubmitting) return;
        const value = name === "speaker" ? input.value === "true" : Number(input.value);
        if (!validDeviceValue(name, value)) return;
        deviceSubmitting = true;
        deviceRevision++;
        deviceMessage = "";
        renderDevice();
        try {
            const next = await request("/api/device", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:"set_" + name, value})}, 10000);
            deviceDirty.delete(name);
            acceptDevice(next);
            // Read-back wins over the submitted value, even when the field retains focus.
            if (validDeviceValue(name, device[deviceFields[name].key])) input.value = String(device[deviceFields[name].key]);
        } catch (error) {
            device = {available:false, supported:{}};
            deviceMessage = error.name === "AbortError" ? "设置请求超时，无法确认结果，正在重新读取设备。" : error.message;
        } finally {
            deviceSubmitting = false;
            deviceRevision++;
            renderDevice();
            scheduleDevice();
        }
    }
    for (const name of Object.keys(deviceFields)) {
        $("device-" + name).addEventListener("input", () => { deviceDirty.add(name); renderDevice(); });
        $("device-" + name + "-form").addEventListener("submit", event => { event.preventDefault(); applyDevice(name); });
    }
    devicePanel.addEventListener("toggle", () => { if (devicePanel.open) pollDevice(); else scheduleDevice(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && devicePanel.open) pollDevice(); else scheduleDevice(); });
    function updateCamera() {
        const show = authorized && page === "home" && cameraSelected && !document.hidden;
        $("camera-frame").hidden = !cameraSelected;
        $("avatar").hidden = cameraSelected;
        const image = $("camera-image");
        if (show && !image.hasAttribute("src")) {
            $("camera-message").hidden = false;
            $("camera-message").textContent = "正在连接摄像头…";
            image.src = "/vision/stream";
        } else if (!show) image.removeAttribute("src");
        $("view-camera").setAttribute("aria-pressed", String(cameraSelected));
        $("view-expression").setAttribute("aria-pressed", String(!cameraSelected));
    }
    function navigate(name) {
        page = ["home", "settings", "status", "logs"].includes(name) ? name : "home";
        document.querySelectorAll(".portal-page").forEach(node => { node.hidden = node.id !== "page-" + page; });
        document.querySelectorAll("[data-page]").forEach(node => {
            if (node.dataset.page === page) node.setAttribute("aria-current", "page");
            else node.removeAttribute("aria-current");
        });
        updateCamera();
        scheduleNetwork();
        scheduleDevice();
        if (authorized && page === "settings") {
            if (networkPanel.open) pollNetwork();
            if (devicePanel.open) pollDevice();
            $("access-qr").src = "/api/access/qr.svg";
        } else $("access-qr").removeAttribute("src");
        if (authorized && page === "logs") loadLogs();
    }
    function showAccessGate(message, pairing) {
        authorized = false;
        $("app-shell").hidden = true;
        $("pair-gate").hidden = false;
        $("access-message").textContent = message;
        $("pair-form").hidden = !pairing;
        $("access-retry").hidden = pairing;
        $("pair-code").value = "";
        $("pairing-code-value").textContent = "";
        $("pairing-code-panel").hidden = true;
        updateCamera();
        $("access-qr").removeAttribute("src");
    }
    async function checkAccess() {
        $("access-retry").disabled = true;
        try {
            access = await request("/api/access");
            if (access.pairing_required && !access.paired) {
                showAccessGate("请输入设备上显示的配对码，授权此浏览器访问。", true);
                return;
            }
            authorized = true;
            $("pair-gate").hidden = true;
            $("app-shell").hidden = false;
            const pairingCode = typeof access.pairing_code === "string" && /^\d{8}$/.test(access.pairing_code) ? access.pairing_code : "";
            $("pairing-code-value").textContent = pairingCode;
            $("pairing-code-panel").hidden = !pairingCode;
            delete access.pairing_code;
            $("device-addresses").textContent = (access.device_addresses || []).map(item =>
                (item.kind === "wifi" ? "无线" : "有线/其他") + "：" + item.url).join(" · ") + "（二维码优先使用无线地址）";
            const link = $("device-url");
            try {
                const target = new URL(access.device_url);
                if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) throw new Error();
                link.href = target.href;
                link.textContent = target.href;
            } catch (_) { link.removeAttribute("href"); link.textContent = "设备地址暂不可用"; }
            navigate(location.hash.slice(1));
        } catch (_) { showAccessGate("暂时无法连接设备，请检查网络后重试。", false); }
        finally { $("access-retry").disabled = false; }
    }
    function logQuery() { return new URLSearchParams({source:$("log-source").value, level:$("log-level").value}).toString(); }
    async function loadLogs() {
        if (!authorized || page !== "logs" || logLoading) return;
        const revision = ++logRevision;
        const query = logQuery();
        logLoading = true;
        $("log-refresh").disabled = true;
        $("log-status").textContent = "正在读取日志…";
        $("log-export").href = "/api/logs/export?" + query;
        try {
            const result = await request("/api/logs?" + query);
            if (!authorized || revision !== logRevision || query !== logQuery()) return;
            const entries = Array.isArray(result.entries) ? result.entries.slice(-200) : [];
            const list = document.createDocumentFragment();
            for (const entry of entries) {
                if (!entry || typeof entry !== "object") continue;
                const row = document.createElement("li");
                const meta = document.createElement("div");
                meta.className = "log-meta";
                meta.textContent = [entry.timestamp, entry.level, entry.source, entry.code].filter(value => typeof value === "string" || typeof value === "number").join(" · ");
                const body = document.createElement("p");
                body.textContent = typeof entry.message === "string" ? entry.message : "";
                row.dataset.level = ["error", "warning"].includes(entry.level) ? entry.level : "info";
                row.append(meta, body); list.append(row);
            }
            $("log-entries").replaceChildren(list);
            $("log-status").textContent = result.available === false || result.supported === false ? (typeof result.reason === "string" && result.reason ? result.reason : "此设备暂不提供该来源的日志。") : entries.length ? "已读取 " + entries.length + " 条事件 · 日志不会自动刷新" : "此筛选条件下没有日志。";
        } catch (_) { $("log-status").textContent = "日志读取失败，请稍后重试。"; }
        finally {
            logLoading = false;
            $("log-refresh").disabled = false;
            if (authorized && page === "logs" && query !== logQuery()) loadLogs();
        }
    }
    document.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => {
        location.hash = button.dataset.page;
        navigate(button.dataset.page);
    }));
    window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
    $("view-camera").addEventListener("click", () => { cameraSelected = true; updateCamera(); });
    $("view-expression").addEventListener("click", () => { cameraSelected = false; updateCamera(); });
    $("camera-image").addEventListener("load", () => { $("camera-message").hidden = true; });
    $("camera-image").addEventListener("error", () => { if (cameraSelected && authorized) { $("camera-message").hidden = false; $("camera-message").textContent = "摄像头画面暂不可用，可切换表情后重试。"; } });
    $("pair-form").addEventListener("submit", async event => {
        event.preventDefault();
        const code = $("pair-code").value.trim();
        $("pair-code").value = "";
        $("pair-submit").disabled = true;
        try {
            await request("/api/pair", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({code})});
            await checkAccess();
        } catch (error) { $("access-message").textContent = error.name === "AbortError" ? "配对超时，请重试。" : error.message; }
        finally { $("pair-submit").disabled = false; }
    });
    $("access-qr").addEventListener("error", () => { $("access-qr-error").hidden = false; });
    $("access-qr").addEventListener("load", () => { $("access-qr-error").hidden = true; });
    $("access-retry").addEventListener("click", checkAccess);
    $("log-refresh").addEventListener("click", loadLogs);
    for (const id of ["log-source", "log-level"]) $(id).addEventListener("change", () => { logRevision++; loadLogs(); });
    render();
    checkAccess();
    poll();
})();

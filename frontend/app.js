// OmniStream Client-Side Streaming Controller (v1.0.3)

let socket = null;
let audioContext = null;
let mediaStream = null;
let scriptProcessor = null;
let analyser = null;
let isStreaming = false;
let cameraActive = false;
let speechRecognizer = null;

// Audio Playback Queue
const audioQueue = [];
let isPlayingAudio = false;
let currentAudioElement = null;
let hasPlayedAnyAudioForTurn = false;

// Active Assistant Message bubble
let currentAssistantMsgElement = null;

// DOM Elements
const statusDot = document.getElementById("statusDot");
const sessionStatus = document.getElementById("sessionStatus");
const metricTtft = document.getElementById("metricTtft");
const metricTtfb = document.getElementById("metricTtfb");
const startBtn = document.getElementById("startBtn");
const interruptBtn = document.getElementById("interruptBtn");
const webcamVideo = document.getElementById("webcamVideo");
const videoPlaceholder = document.getElementById("videoPlaceholder");
const toggleCameraBtn = document.getElementById("toggleCameraBtn");
const waveformCanvas = document.getElementById("waveformCanvas");
const chatBox = document.getElementById("chatBox");
const streamingBubble = document.getElementById("streamingBubble");
const streamingText = document.getElementById("streamingText");
const textForm = document.getElementById("textForm");
const textInput = document.getElementById("textInput");
const hiddenSnapshotCanvas = document.getElementById("hiddenSnapshotCanvas");
const testAudioBtn = document.getElementById("testAudioBtn");

if (testAudioBtn) {
    testAudioBtn.addEventListener("click", () => {
        speakWithBrowserTTS("Audio test successful! Your speakers and browser audio are working perfectly.");
        sessionStatus.innerText = "Speaker Test Active";
    });
}

// Settings Modal Elements
const openSettingsBtn = document.getElementById("openSettingsBtn");
const closeSettingsBtn = document.getElementById("closeSettingsBtn");
const settingsModal = document.getElementById("settingsModal");
const saveKeysBtn = document.getElementById("saveKeysBtn");
const geminiKeyInput = document.getElementById("geminiKeyInput");

// Load stored Gemini key
const savedGeminiKey = localStorage.getItem("omnistream_gemini_key") || "";
if (geminiKeyInput && savedGeminiKey) {
    geminiKeyInput.value = savedGeminiKey;
}

if (openSettingsBtn) openSettingsBtn.addEventListener("click", () => settingsModal.classList.remove("hidden"));
if (closeSettingsBtn) closeSettingsBtn.addEventListener("click", () => settingsModal.classList.add("hidden"));
if (saveKeysBtn) {
    saveKeysBtn.addEventListener("click", () => {
        const key = geminiKeyInput.value.trim();
        localStorage.setItem("omnistream_gemini_key", key);
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "set_keys", gemini_key: key }));
        }
        settingsModal.classList.add("hidden");
        sessionStatus.innerText = "API Key Saved";
    });
}

// Guaranteed Speech Synthesis Fallback (Browser-level voice)
function speakWithBrowserTTS(text) {
    if (!window.speechSynthesis || !text) return;
    try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.05;
        utterance.pitch = 1.0;
        utterance.onstart = () => { isPlayingAudio = true; };
        utterance.onend = () => { isPlayingAudio = false; };
        utterance.onerror = () => { isPlayingAudio = false; };
        window.speechSynthesis.speak(utterance);
    } catch (e) {
        console.warn("Browser speech synthesis error:", e);
    }
}

// Downsampling helper to 16kHz Int16 PCM
function downsampleBuffer(buffer, inputSampleRate, outputSampleRate = 16000) {
    if (inputSampleRate === outputSampleRate) {
        const result = new Int16Array(buffer.length);
        for (let i = 0; i < buffer.length; i++) {
            let s = Math.max(-1, Math.min(1, buffer[i]));
            result[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return result.buffer;
    }

    const sampleRateRatio = inputSampleRate / outputSampleRate;
    const newLength = Math.round(buffer.length / sampleRateRatio);
    const result = new Int16Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;

    while (offsetResult < result.length) {
        const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
        let accum = 0, count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
            accum += buffer[i];
            count++;
        }
        let s = count > 0 ? accum / count : 0;
        s = Math.max(-1, Math.min(1, s));
        result[offsetResult] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        offsetResult++;
        offsetBuffer = nextOffsetBuffer;
    }
    return result.buffer;
}

// Oscilloscope Waveform visualizer
function drawWaveform() {
    if (!analyser) return;
    const canvasCtx = waveformCanvas.getContext("2d");
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function render() {
        requestAnimationFrame(render);
        analyser.getByteTimeDomainData(dataArray);

        canvasCtx.fillStyle = "#020617";
        canvasCtx.fillRect(0, 0, waveformCanvas.width, waveformCanvas.height);

        canvasCtx.lineWidth = 2;
        canvasCtx.strokeStyle = isStreaming ? "#6366f1" : "#334155";
        canvasCtx.beginPath();

        const sliceWidth = waveformCanvas.width / bufferLength;
        let x = 0;

        for (let i = 0; i < bufferLength; i++) {
            const v = dataArray[i] / 128.0;
            const y = (v * waveformCanvas.height) / 2;
            if (i === 0) canvasCtx.moveTo(x, y);
            else canvasCtx.lineTo(x, y);
            x += sliceWidth;
        }
        canvasCtx.lineTo(waveformCanvas.width, waveformCanvas.height / 2);
        canvasCtx.stroke();
    }
    render();
}

// Snapshot webcam frame
function captureWebcamFrame() {
    if (!cameraActive || !webcamVideo.videoWidth) return null;
    const ctx = hiddenSnapshotCanvas.getContext("2d");
    hiddenSnapshotCanvas.width = 320;
    hiddenSnapshotCanvas.height = (webcamVideo.videoHeight / webcamVideo.videoWidth) * 320;
    ctx.drawImage(webcamVideo, 0, 0, hiddenSnapshotCanvas.width, hiddenSnapshotCanvas.height);
    const dataUrl = hiddenSnapshotCanvas.toDataURL("image/jpeg", 0.6);

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "video_frame", image: dataUrl }));
    }
    return dataUrl;
}

// Reliable HTML5 Audio Player for complete sentence MP3 blobs
function playNextInAudioQueue() {
    if (audioQueue.length === 0) {
        isPlayingAudio = false;
        if (interruptBtn) interruptBtn.disabled = true;
        return;
    }

    isPlayingAudio = true;
    hasPlayedAnyAudioForTurn = true;
    if (interruptBtn) interruptBtn.disabled = false;
    const audioBlob = audioQueue.shift();
    const audioUrl = URL.createObjectURL(audioBlob);

    const audio = new Audio(audioUrl);
    currentAudioElement = audio;

    audio.onended = () => {
        URL.revokeObjectURL(audioUrl);
        currentAudioElement = null;
        playNextInAudioQueue();
    };

    audio.onerror = () => {
        URL.revokeObjectURL(audioUrl);
        currentAudioElement = null;
        playNextInAudioQueue();
    };

    audio.play().catch(err => {
        console.warn("HTML5 audio playback blocked:", err);
        playNextInAudioQueue();
    });
}

function flushAudioQueue() {
    audioQueue.length = 0;
    if (currentAudioElement) {
        try {
            currentAudioElement.pause();
            currentAudioElement.currentTime = 0;
        } catch (_) {}
        currentAudioElement = null;
    }
    if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
    }
    isPlayingAudio = false;
    if (interruptBtn) interruptBtn.disabled = true;
}

// Append Chat Message
function appendChatMessage(sender, text) {
    const msg = document.createElement("div");
    msg.className = `p-3.5 rounded-xl text-xs max-w-[85%] ${
        sender === "user" 
            ? "ml-auto bg-indigo-600 text-white" 
            : "mr-auto bg-slate-800 text-slate-200 border border-slate-700/60"
    }`;
    msg.innerHTML = `<strong>${sender === "user" ? "You" : "Assistant"}:</strong> <span class="content">${text}</span>`;
    chatBox.appendChild(msg);
    chatBox.scrollTop = chatBox.scrollHeight;
    return msg;
}

// Web Speech API
function initBrowserSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    speechRecognizer = new SpeechRecognition();
    speechRecognizer.continuous = true;
    speechRecognizer.interimResults = false;
    speechRecognizer.lang = "en-US";

    speechRecognizer.onresult = (event) => {
        if (isPlayingAudio) return;

        for (let i = event.resultIndex; i < event.results.length; ++i) {
            if (event.results[i].isFinal) {
                const transcript = event.results[i][0].transcript.trim();
                if (transcript.length > 1) {
                    console.log("Web Speech recognized:", transcript);
                    captureWebcamFrame();
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            type: "speech_recognized",
                            text: transcript
                        }));
                    }
                }
            }
        }
    };

    speechRecognizer.onerror = (err) => {
        console.warn("Speech recognition notice:", err.error);
    };

    speechRecognizer.onend = () => {
        if (isStreaming) {
            try { speechRecognizer.start(); } catch (_) {}
        }
    };

    try { speechRecognizer.start(); } catch (_) {}
}

// WebSocket Connection with Automatic Reconnection
function initWebSocket() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
        return;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/ws/stream`);
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
        statusDot.className = "w-3 h-3 rounded-full bg-emerald-500 animate-pulse";
        sessionStatus.innerText = "Connected";

        const key = localStorage.getItem("omnistream_gemini_key");
        if (key) {
            socket.send(JSON.stringify({ type: "set_keys", gemini_key: key }));
        }
    };

    socket.onmessage = (event) => {
        if (typeof event.data === "string") {
            const data = JSON.parse(event.data);
            
            if (data.type === "status") {
                sessionStatus.innerText = data.status;
            } else if (data.type === "metric_ttft") {
                metricTtft.innerText = `${data.value_ms} ms`;
            } else if (data.type === "metric_ttfb") {
                metricTtfb.innerText = `${data.value_ms} ms`;
            } else if (data.type === "user_transcription") {
                appendChatMessage("user", data.text);
                hasPlayedAnyAudioForTurn = false;
            } else if (data.type === "ai_start") {
                streamingBubble.classList.remove("hidden");
                streamingText.innerText = "Assistant is speaking...";
                currentAssistantMsgElement = appendChatMessage("assistant", "");
            } else if (data.type === "ai_text_chunk") {
                if (!currentAssistantMsgElement) {
                    currentAssistantMsgElement = appendChatMessage("assistant", "");
                }
                const span = currentAssistantMsgElement.querySelector(".content");
                if (span) span.innerText += data.chunk;
                chatBox.scrollTop = chatBox.scrollHeight;
            } else if (data.type === "ai_complete") {
                streamingBubble.classList.add("hidden");
                if (currentAssistantMsgElement) {
                    const span = currentAssistantMsgElement.querySelector(".content");
                    if (span && !span.innerText.trim()) {
                        span.innerText = data.full_text;
                    }
                }
                
                // Fallback: If no server MP3 was played, speak out loud with browser voice
                if (!hasPlayedAnyAudioForTurn && data.full_text) {
                    console.log("Playing browser voice fallback...");
                    speakWithBrowserTTS(data.full_text);
                }

                currentAssistantMsgElement = null;
                sessionStatus.innerText = `Idle (${data.total_latency_ms}ms)`;
            } else if (data.type === "interrupt") {
                flushAudioQueue();
                sessionStatus.innerText = "Interrupted (Barge-in)";
            }
        } else {
            // Incoming complete sentence MP3 audio clip
            const blob = new Blob([event.data], { type: "audio/mpeg" });
            audioQueue.push(blob);
            if (!isPlayingAudio) {
                playNextInAudioQueue();
            }
        }
    };

    socket.onclose = () => {
        statusDot.className = "w-3 h-3 rounded-full bg-rose-500";
        sessionStatus.innerText = "Disconnected";
        // Auto-reconnect after 1 second if session is active
        if (isStreaming) {
            setTimeout(initWebSocket, 1000);
        }
    };
}

// Start Stream
startBtn.addEventListener("click", async () => {
    if (isStreaming) {
        isStreaming = false;
        startBtn.innerText = "Start Stream";
        startBtn.className = "px-5 py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-semibold text-sm transition";
        if (scriptProcessor) scriptProcessor.disconnect();
        if (speechRecognizer) {
            try { speechRecognizer.stop(); } catch (_) {}
        }
        if (socket) socket.close();
        flushAudioQueue();
        return;
    }

    try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (audioContext.state === "suspended") {
            await audioContext.resume();
        }
        initWebSocket();

        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
            video: true
        });

        webcamVideo.srcObject = mediaStream;
        cameraActive = true;
        videoPlaceholder.classList.add("hidden");

        const source = audioContext.createMediaStreamSource(mediaStream);
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        drawWaveform();

        scriptProcessor = audioContext.createScriptProcessor(2048, 1, 1);
        source.connect(scriptProcessor);
        scriptProcessor.connect(audioContext.destination);

        scriptProcessor.onaudioprocess = (e) => {
            if (!isStreaming || !socket || socket.readyState !== WebSocket.OPEN) return;
            if (isPlayingAudio) return;

            const inputData = e.inputBuffer.getChannelData(0);
            const pcmBuffer = downsampleBuffer(inputData, audioContext.sampleRate, 16000);
            socket.send(pcmBuffer);
        };

        initBrowserSpeechRecognition();
        setInterval(captureWebcamFrame, 1500);

        isStreaming = true;
        startBtn.innerText = "Stop Stream";
        startBtn.className = "px-5 py-2.5 rounded-xl bg-rose-500 hover:bg-rose-400 text-white font-semibold text-sm transition";

    } catch (err) {
        alert("Microphone/Camera permission required: " + err.message);
    }
});

// Interrupt Button
if (interruptBtn) {
    interruptBtn.addEventListener("click", () => {
        flushAudioQueue();
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "interrupt" }));
        }
    });
}

// Manual Text Submit
textForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = textInput.value.trim();
    if (!text) return;
    captureWebcamFrame();
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "manual_text", text }));
    }
    textInput.value = "";
});

// Toggle Camera Button
toggleCameraBtn.addEventListener("click", () => {
    if (mediaStream) {
        const videoTrack = mediaStream.getVideoTracks()[0];
        if (videoTrack) {
            videoTrack.enabled = !videoTrack.enabled;
            cameraActive = videoTrack.enabled;
            videoPlaceholder.classList.toggle("hidden", cameraActive);
        }
    }
});

// Auto-connect socket on initial page load
window.addEventListener("DOMContentLoaded", () => {
    initWebSocket();
});

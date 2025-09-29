// client.js – WebRTC клиент для Camera WebRTC Microservice

class CameraClient {
    constructor(baseUrl = "") {
        this.baseUrl = baseUrl || this.getBaseUrl();
        this.pc = null;
        this.dataChannel = null;
        this.clientId = null;

        this.retryCount = 0;
        this.maxRetries = 3;
        this.retryTimeout = null;

        this.qualityInterval = null;
        
        // Защита от множественных подключений
        this.isConnecting = false;
        this.connectDebounceTimeout = null;
        
        // Проверка на множественные вкладки
        this.tabId = Math.random().toString(36).substr(2, 9);
        this.checkMultipleTabs();
    }

    // Функция для определения базового URL
    getBaseUrl() {
        // Проверяем, работаем ли мы через прокси
        if (window.location.pathname.startsWith('/api/v1/color_camera')) {
            return '/api/v1/color_camera';
        }
        return '';
    }

    async fetchIceConfig() {
        try {
            const baseUrl = this.getBaseUrl();
            const res = await fetch(`${baseUrl}/ice_config`);
            if (!res.ok) throw new Error("Failed to fetch ICE config");
            const cfg = await res.json();

            const servers = [];
            if (cfg.urls && cfg.urls.length) {
                servers.push({
                    urls: cfg.urls,
                    username: cfg.username || undefined,
                    credential: cfg.credential || undefined
                });
            }

            return { iceServers: servers };
        } catch (e) {
            console.warn("Using fallback ICE config:", e);
            return {
                iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
            };
        }
    }

    async connect(mode = "color") {
        console.log(`[Tab ${this.tabId}] Connect called with mode: ${mode}`);
        
        // ГЛОБАЛЬНАЯ защита от множественных подключений
        if (globalConnecting) {
            console.log(`[Tab ${this.tabId}] Global connection in progress, ignoring request`);
            return;
        }
        
        // СТРОГАЯ защита от множественных подключений
        if (this.isConnecting) {
            console.log(`[Tab ${this.tabId}] Already connecting, ignoring request`);
            return;
        }
        
        // Проверяем, не подключены ли уже
        if (this.pc && (this.pc.connectionState === "connected" || this.pc.connectionState === "connecting")) {
            console.log(`[Tab ${this.tabId}] Already connected/connecting (${this.pc.connectionState}), ignoring request`);
            return;
        }
        
        // Проверяем, есть ли активный clientId
        if (this.clientId) {
            console.log(`[Tab ${this.tabId}] Already have clientId (${this.clientId}), ignoring request`);
            return;
        }
        
        // Устанавливаем глобальный флаг
        globalConnecting = true;
        
        // Дебаунсинг - отменяем предыдущий запрос если он еще не выполнился
        if (this.connectDebounceTimeout) {
            clearTimeout(this.connectDebounceTimeout);
        }
        
        this.isConnecting = true;
        this._updateConnectionUI();
        
        // Запускаем анимацию прогресса
        this._startProgressAnimation();
        
        try {
            // Очистка перед новым подключением (НЕ сбрасываем isConnecting)
        await this.close();

        const config = await this.fetchIceConfig();
        this.pc = new RTCPeerConnection(config);

        // Медиа-треки
        this.pc.ontrack = (event) => {
            const stream = event.streams[0];
            if (event.track.kind === "video") {
                document.getElementById("video").srcObject = stream;
            } else if (event.track.kind === "audio") {
                document.getElementById("audio").srcObject = stream;
            }
        };

        // ICE candidates
        this.pc.onicecandidate = (event) => {
            if (event.candidate && this.clientId) {
                // Детальное логирование ICE candidates
                const candidate = event.candidate;
                console.log(`[Tab ${this.tabId}] ICE Candidate:`, {
                    type: candidate.type,
                    protocol: candidate.protocol,
                    address: candidate.address,
                    port: candidate.port,
                    candidateType: candidate.candidateType,
                    priority: candidate.priority,
                    foundation: candidate.foundation,
                    component: candidate.component,
                    relatedAddress: candidate.relatedAddress,
                    relatedPort: candidate.relatedPort,
                    tcpType: candidate.tcpType,
                    url: candidate.url
                });
                
                // Отправляем на сервер
                const baseUrl = this.getBaseUrl();
                this._post(`${baseUrl}/ice`, {
                    client_id: this.clientId,
                    candidate: candidate.candidate,
                    sdp_mid: candidate.sdpMid,
                    sdp_mline_index: candidate.sdpMLineIndex
                });
            } else if (event.candidate === null) {
                console.log(`[Tab ${this.tabId}] ICE gathering completed`);
            }
        };

        // Connection state
        this.pc.onconnectionstatechange = () => {
            console.log(`[Tab ${this.tabId}] Connection state:`, this.pc.connectionState);
            this._updateConnectionUI();
            
            if (this.pc.connectionState === "connected") {
                this.retryCount = 0;
                this._updateProgress(100, "Connected!");
                setTimeout(() => {
                    this._updateConnectionUI(); // Скрываем прогресс-бар
                }, 1000);
                this._startQualityMonitor();
            } else if (["failed", "disconnected", "closed"].includes(this.pc.connectionState)) {
                // НЕ переподключаемся автоматически - пользователь должен сам нажать кнопку
                console.log(`[Tab ${this.tabId}] Connection lost, waiting for manual reconnect`);
                this._stopQualityMonitor();
                this._updateConnectionUI(); // Скрываем прогресс-бар
            }
        };
        
        // ICE gathering state
        this.pc.onicegatheringstatechange = () => {
            console.log(`[Tab ${this.tabId}] ICE gathering state:`, this.pc.iceGatheringState);
        };
        
        // ICE connection state
        this.pc.oniceconnectionstatechange = () => {
            console.log(`[Tab ${this.tabId}] ICE connection state:`, this.pc.iceConnectionState);
        };

        // DataChannel (для overlay)
        if (mode === "overlay") {
            this.dataChannel = this.pc.createDataChannel("control");
            this.dataChannel.onmessage = (e) => {
                try {
                    const msg = JSON.parse(e.data);
                    if (msg.type === "depth") {
                        console.log("Depth data:", msg);
                    }
                } catch {
                    console.log("DataChannel message:", e.data);
                }
            };
        }

        this.pc.addTransceiver("video", { direction: "recvonly" });
        this.pc.addTransceiver("audio", { direction: "recvonly" });

        const offer = await this.pc.createOffer();
        await this.pc.setLocalDescription(offer);

        const baseUrl = this.getBaseUrl();
        const answer = await this._post(`${baseUrl}/offer`, {
            sdp: offer.sdp,
            type: offer.type,
            mode
        });

        this.clientId = answer.client_id;
        await this.pc.setRemoteDescription(answer);
        console.log(`[Tab ${this.tabId}] Connected with client_id:`, this.clientId);
        
        } catch (error) {
            console.error("Connection failed:", error);
            this._updateProgress(0, "Connection failed");
            setTimeout(() => {
                this._updateConnectionUI(); // Скрываем прогресс-бар
            }, 2000);
            await this.close();
        } finally {
            this.isConnecting = false;
            globalConnecting = false; // Сбрасываем глобальный флаг
            this._updateConnectionUI();
        }
    }

    async getDepthAt(x, y, percent = true) {
        if (!this.clientId) throw new Error("Not connected");
        const baseUrl = this.getBaseUrl();
        const url = `${baseUrl}/depth_at_point?client_id=${this.clientId}&x=${x}&y=${y}&percent=${percent}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to fetch depth");
        return res.json();
    }

    async close() {
        console.log(`Close called for client ${this.clientId}`);
        
        // Очищаем все таймауты
        if (this.retryTimeout) {
            clearTimeout(this.retryTimeout);
            this.retryTimeout = null;
        }
        if (this.connectDebounceTimeout) {
            clearTimeout(this.connectDebounceTimeout);
            this.connectDebounceTimeout = null;
        }
        
        if (this.pc) {
            this._stopQualityMonitor();
            this.pc.close();
            this.pc = null;
        }
        
        // Server automatically handles disconnection when new client connects
        // No need to send DELETE request as it causes 404 errors
        
        this.clientId = null;
        this.dataChannel = null;
        
        // НЕ сбрасываем isConnecting здесь - это делается в connect() finally блоке
        this._updateConnectionUI();
    }

    async forceCleanup() {
        const baseUrl = this.getBaseUrl();
        await fetch(`${baseUrl}/cleanup`, { method: "POST" });
        await this.close();
    }

    async manualClose() {
        // Manual close - notify server explicitly
        if (this.clientId) {
            console.log(`Manual close: sending DELETE for client ${this.clientId}`);
            try {
                const baseUrl = this.getBaseUrl();
                await fetch(`${baseUrl}/connections/${this.clientId}`, { method: "DELETE" });
                console.log(`Manual close: DELETE successful for client ${this.clientId}`);
            } catch (e) {
                console.warn("Failed to notify server about manual disconnection:", e);
            }
        }
        await this.close();
    }
    
    // Функция для получения детальной статистики соединения
    async getConnectionStats() {
        if (!this.pc) {
            console.log("No active connection");
            return;
        }
        
        try {
            const stats = await this.pc.getStats();
            console.log("=== CONNECTION STATISTICS ===");
            
            stats.forEach(report => {
                if (report.type === 'local-candidate' || report.type === 'remote-candidate') {
                    console.log(`${report.type.toUpperCase()}:`, {
                        id: report.id,
                        candidateType: report.candidateType,
                        protocol: report.protocol,
                        address: report.address,
                        port: report.port,
                        priority: report.priority,
                        url: report.url,
                        relayProtocol: report.relayProtocol
                    });
                } else if (report.type === 'candidate-pair') {
                    console.log('CANDIDATE PAIR:', {
                        id: report.id,
                        state: report.state,
                        nominated: report.nominated,
                        priority: report.priority,
                        localCandidateId: report.localCandidateId,
                        remoteCandidateId: report.remoteCandidateId
                    });
                }
            });
            
            return stats;
        } catch (error) {
            console.error("Failed to get connection stats:", error);
        }
    }

    async _post(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
        if (!res.ok) throw new Error(`POST ${url} failed`);
        return res.json();
    }

    // _handleRetry удален - больше не переподключаемся автоматически

    _startQualityMonitor() {
        this._stopQualityMonitor();
        this.qualityInterval = setInterval(async () => {
            if (!this.pc) return;
            const stats = await this.pc.getStats();
            stats.forEach((report) => {
                if (report.type === "inbound-rtp" && report.kind === "video") {
                    console.log(`Video: ${report.framesPerSecond || 0}fps, loss=${report.packetsLost}`);
                }
            });
        }, 5000);
    }

    _stopQualityMonitor() {
        if (this.qualityInterval) {
            clearInterval(this.qualityInterval);
            this.qualityInterval = null;
        }
    }

    _updateConnectionUI() {
        const closeBtn = document.getElementById("close-connection-btn");
        const playBtn = document.getElementById("play-btn");
        const progressBar = document.getElementById("connection-progress");
        
        console.log(`[Tab ${this.tabId}] UI Update - isConnecting: ${this.isConnecting}, connectionState: ${this.pc?.connectionState}`);
        
        if (this.isConnecting) {
            // Показываем состояние подключения
            closeBtn.disabled = true;
            playBtn.style.display = "none"; // Скрываем play при подключении
            if (progressBar) {
                progressBar.style.display = "block"; // Показываем прогресс-бар
                // Добавляем анимацию появления
                setTimeout(() => {
                    progressBar.classList.add('show');
                }, 10);
                console.log(`[Tab ${this.tabId}] Progress bar shown`);
            }
        } else if (this.pc && this.pc.connectionState === "connected") {
            // Подключены - скрываем play и прогресс, показываем close
            closeBtn.disabled = false;
            playBtn.style.display = "none";
            if (progressBar) {
                // Показываем "Connected!" на 2 секунды, потом скрываем
                this._updateProgress(100, "Connected!");
                setTimeout(() => {
                    progressBar.classList.remove('show');
                    setTimeout(() => {
                        progressBar.style.display = "none";
                    }, 300); // Ждем завершения анимации
                }, 2000); // Показываем 2 секунды
                console.log(`[Tab ${this.tabId}] Progress bar will be hidden in 2 seconds`);
            }
        } else {
            // Не подключены - показываем play, скрываем close и прогресс
            closeBtn.disabled = true;
            playBtn.style.display = "block";
            playBtn.disabled = false; // Включаем кнопку
            playBtn.style.opacity = "1";
            playBtn.style.cursor = "pointer";
            if (progressBar) {
                progressBar.classList.remove('show');
                setTimeout(() => {
                    progressBar.style.display = "none";
                }, 300); // Ждем завершения анимации
                console.log(`[Tab ${this.tabId}] Progress bar hidden - disconnected`);
            }
        }
    }

    _updateProgress(percent, status) {
        const progressCircle = document.querySelector('.progress-ring-circle');
        const progressPercent = document.getElementById('progress-percent');
        const progressStatus = document.getElementById('progress-status');
        
        console.log(`[Tab ${this.tabId}] Progress: ${percent}% - ${status}`);
        
        if (progressCircle && progressPercent && progressStatus) {
            const circumference = 2 * Math.PI * 60; // r=60
            const offset = circumference - (percent / 100) * circumference;
            progressCircle.style.strokeDashoffset = offset;
            progressPercent.textContent = `${Math.round(percent)}%`;
            progressStatus.textContent = status;
            console.log(`[Tab ${this.tabId}] Progress updated successfully`);
        } else {
            console.error(`[Tab ${this.tabId}] Progress elements not found!`, {
                progressCircle: !!progressCircle,
                progressPercent: !!progressPercent,
                progressStatus: !!progressStatus
            });
        }
    }

    _startProgressAnimation() {
        console.log(`[Tab ${this.tabId}] Starting progress animation`);
        
        // Проверяем, что элементы существуют
        const progressBar = document.getElementById("connection-progress");
        const progressCircle = document.querySelector('.progress-ring-circle');
        const progressPercent = document.getElementById('progress-percent');
        const progressStatus = document.getElementById('progress-status');
        
        console.log(`[Tab ${this.tabId}] Progress elements check:`, {
            progressBar: !!progressBar,
            progressCircle: !!progressCircle,
            progressPercent: !!progressPercent,
            progressStatus: !!progressStatus
        });
        
        if (!progressBar || !progressCircle || !progressPercent || !progressStatus) {
            console.error(`[Tab ${this.tabId}] Progress elements not found!`);
            return;
        }
        
        // Прогресс-бар уже показан в _updateConnectionUI
        
        // Инициализируем прогресс-бар
        this._updateProgress(0, "Starting...");
        
        const steps = [
            { percent: 20, status: "Initializing..." },
            { percent: 40, status: "Fetching ICE config..." },
            { percent: 60, status: "Creating connection..." },
            { percent: 80, status: "Establishing stream..." },
            { percent: 95, status: "Almost ready..." }
        ];
        
        let currentStep = 0;
        
        const animate = () => {
            if (currentStep < steps.length) {
                const step = steps[currentStep];
                console.log(`[Tab ${this.tabId}] Animation step ${currentStep + 1}: ${step.percent}% - ${step.status}`);
                this._updateProgress(step.percent, step.status);
                currentStep++;
                setTimeout(animate, 1000); // Увеличиваем до 1 секунды для тестирования
            } else {
                // Достигли 95%, ждем реального подключения
                console.log(`[Tab ${this.tabId}] Animation completed, waiting for connection`);
                this._updateProgress(95, "Finalizing...");
            }
        };
        
        // Начинаем анимацию
        console.log(`[Tab ${this.tabId}] Starting animation in 500ms`);
        setTimeout(animate, 500);
    }

    checkMultipleTabs() {
        // Проверяем доступность localStorage
        if (!this.isLocalStorageAvailable()) {
            console.warn("localStorage not available, skipping multiple tabs detection");
            return;
        }
        
        // Простая проверка на множественные вкладки через localStorage
        const storageKey = 'camera_client_tab';
        const currentTime = Date.now();
        
        try {
            // Сохраняем информацию о текущей вкладке
            localStorage.setItem(storageKey, JSON.stringify({
                tabId: this.tabId,
                timestamp: currentTime
            }));
            
            // Проверяем, есть ли другие активные вкладки
            const checkInterval = setInterval(() => {
                try {
                    const stored = localStorage.getItem(storageKey);
                    if (stored) {
                        const data = JSON.parse(stored);
                        // Если другая вкладка обновила timestamp недавно
                        if (data.tabId !== this.tabId && (currentTime - data.timestamp) < 5000) {
                            console.warn("⚠️ Multiple tabs detected! Only one tab should be connected to the camera.");
                            // Можно показать уведомление пользователю
                        }
                    }
                    
                    // Обновляем timestamp текущей вкладки
                    localStorage.setItem(storageKey, JSON.stringify({
                        tabId: this.tabId,
                        timestamp: Date.now()
                    }));
                } catch (e) {
                    console.warn("Error checking multiple tabs:", e);
                    clearInterval(checkInterval);
                }
            }, 2000);
            
            // Очищаем при закрытии вкладки
            window.addEventListener('beforeunload', () => {
                clearInterval(checkInterval);
                try {
                    const stored = localStorage.getItem(storageKey);
                    if (stored) {
                        const data = JSON.parse(stored);
                        if (data.tabId === this.tabId) {
                            localStorage.removeItem(storageKey);
                        }
                    }
                } catch (e) {
                    console.warn("Error cleaning up localStorage:", e);
                }
            });
        } catch (e) {
            console.warn("Error initializing multiple tabs detection:", e);
        }
    }

    isLocalStorageAvailable() {
        try {
            const test = '__localStorage_test__';
            localStorage.setItem(test, test);
            localStorage.removeItem(test);
            return true;
        } catch (e) {
            return false;
        }
    }
}

// ---------- Глобальная защита от множественных подключений ----------
let globalConnecting = false;

// ---------- Глобальный экземпляр клиента ----------
const client = new CameraClient();

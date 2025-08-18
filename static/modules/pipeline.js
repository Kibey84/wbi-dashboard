// modules/pipeline.js - Pipeline management
export class PipelineManager {
    constructor({ state, notifications, utils }) {
        this.state = state;
        this.notifications = notifications;
        this.utils = utils;
        
        this.elements = {};
        this.pollingInterval = null;
        this.isRunning = false;
        
        // Bind methods
        this.runPipeline = this.runPipeline.bind(this);
        this.checkStatus = this.checkStatus.bind(this);
        this.handleDownload = this.handleDownload.bind(this);
    }

    async init() {
        try {
            this.cacheElements();
            this.bindEvents();
            this.setupStateSubscriptions();
            
            console.log('[Pipeline] Manager initialized');
        } catch (error) {
            console.error('[Pipeline] Initialization failed:', error);
            throw error;
        }
    }

    cacheElements() {
        this.elements = {
            runButton: document.getElementById('runPipelineBtn'),
            progressContainer: document.getElementById('pipelineProgress'),
            logContainer: document.getElementById('logContainer'),
            oppsButton: document.getElementById('downloadOppsBtn'),
            matchButton: document.getElementById('downloadMatchBtn')
        };

        // Validate required elements
        const required = ['runButton', 'logContainer'];
        const missing = required.filter(key => !this.elements[key]);
        
        if (missing.length > 0) {
            throw new Error(`Pipeline elements missing: ${missing.join(', ')}`);
        }
    }

    bindEvents() {
        if (this.elements.runButton) {
            this.elements.runButton.addEventListener('click', this.runPipeline);
        }
    }

    setupStateSubscriptions() {
        // Subscribe to pipeline state changes
        this.state.subscribe('pipeline', (pipelineState) => {
            this.updateUI(pipelineState);
        });
    }

    async runPipeline() {
        if (this.isRunning) {
            this.notifications.warning('Pipeline is already running');
            return;
        }

        try {
            this.isRunning = true;
            this.updateButtonState(true);
            this.showProgress();
            this.clearLog();
            this.addLogEntry('🚀 Starting pipeline...', 'info');

            // Reset state
            this.state.batch({
                'pipeline.status': 'running',
                'pipeline.log': [],
                'pipeline.reports.opportunities': null,
                'pipeline.reports.matchmaking': null
            });

            // Start pipeline
            const response = await this.utils.request('/api/run-pipeline', {
                method: 'POST'
            });

            const result = await response.json();
            
            if (result.job_id) {
                this.state.set('pipeline.currentJob', result.job_id);
                this.startPolling(result.job_id);
                this.notifications.info('Pipeline started successfully');
            } else {
                throw new Error('Failed to get job ID from server');
            }

        } catch (error) {
            console.error('[Pipeline] Start failed:', error);
            this.handleError('Failed to start pipeline', error);
            this.isRunning = false;
            this.updateButtonState(false);
            this.hideProgress();
        }
    }

    startPolling(jobId) {
        // Clear any existing polling
        this.stopPolling();
        
        this.pollingInterval = setInterval(() => {
            this.checkStatus(jobId);
        }, 3000);

        // Initial check
        this.checkStatus(jobId);
    }

    stopPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
    }

    async checkStatus(jobId) {
        try {
            const response = await this.utils.request(`/api/pipeline-status/${jobId}`);
            
            if (!response.ok) {
                if (response.status === 404) {
                    this.handleError('Pipeline job not found', new Error('Job may have expired'));
                } else {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return;
            }

            const data = await response.json();
            this.processPipelineUpdate(data);

        } catch (error) {
            console.error('[Pipeline] Status check failed:', error);
            this.addLogEntry(`❌ Error checking status: ${error.message}`, 'error');
        }
    }

    processPipelineUpdate(data) {
        // Update state
        this.state.batch({
            'pipeline.status': data.status,
            'pipeline.log': data.log || [],
            'pipeline.reports.opportunities': data.opps_report_filename,
            'pipeline.reports.matchmaking': data.match_report_filename
        });

        // Update log display
        this.updateLogDisplay(data.log || []);

        // Handle completion
        if (data.status === 'completed' || data.status === 'failed') {
            this.handleCompletion(data);
        }
    }

    handleCompletion(data) {
        this.stopPolling();
        this.isRunning = false;
        this.updateButtonState(false);

        if (data.status === 'completed') {
            this.notifications.success('Pipeline completed successfully');
            this.setupDownloadButtons(data);
        } else if (data.status === 'failed') {
            this.notifications.error('Pipeline failed. Check logs for details.');
            this.addLogEntry('❌ Pipeline failed. Check server logs for details.', 'error');
        }
    }

    setupDownloadButtons(data) {
        if (data.opps_report_filename && this.elements.oppsButton) {
            this.elements.oppsButton.disabled = false;
            this.elements.oppsButton.onclick = () => {
                this.handleDownload(data.opps_report_filename, 'opportunities report');
            };
        }

        if (data.match_report_filename && this.elements.matchButton) {
            this.elements.matchButton.disabled = false;
            this.elements.matchButton.onclick = () => {
                this.handleDownload(data.match_report_filename, 'matchmaking report');
            };
        }
    }

    handleDownload(filename, description) {
        try {
            window.location.href = `/download/${encodeURIComponent(filename)}`;
            this.notifications.success(`Downloading ${description}`);
        } catch (error) {
            console.error('[Pipeline] Download failed:', error);
            this.notifications.error(`Failed to download ${description}: ${error.message}`);
        }
    }

    updateUI(pipelineState) {
        // Update button states based on pipeline status
        if (pipelineState.status === 'running') {
            this.updateButtonState(true);
            this.showProgress();
        } else {
            this.updateButtonState(false);
            this.hideProgress();
        }

        // Update download buttons
        this.updateDownloadButtons(pipelineState.reports);
    }

    updateButtonState(running) {
        if (this.elements.runButton) {
            this.elements.runButton.disabled = running;
            this.elements.runButton.textContent = running ? 'Pipeline Running...' : 'Run Pipeline';
        }

        // Disable download buttons when running
        if (this.elements.oppsButton) {
            this.elements.oppsButton.disabled = running || !this.state.get('pipeline.reports.opportunities');
        }
        if (this.elements.matchButton) {
            this.elements.matchButton.disabled = running || !this.state.get('pipeline.reports.matchmaking');
        }
    }

    updateDownloadButtons(reports) {
        if (this.elements.oppsButton) {
            this.elements.oppsButton.disabled = !reports.opportunities;
        }
        if (this.elements.matchButton) {
            this.elements.matchButton.disabled = !reports.matchmaking;
        }
    }

    showProgress() {
        if (this.elements.progressContainer) {
            this.elements.progressContainer.classList.remove('hidden');
        }
    }

    hideProgress() {
        if (this.elements.progressContainer) {
            this.elements.progressContainer.classList.add('hidden');
        }
    }

    clearLog() {
        if (this.elements.logContainer) {
            this.elements.logContainer.innerHTML = '';
        }
    }

    addLogEntry(message, level = 'info') {
        if (!this.elements.logContainer) return;

        const entry = document.createElement('div');
        entry.className = `log-entry text-gray-300 ${this.getLogLevelClass(level)}`;
        entry.textContent = `${new Date().toLocaleTimeString()} - ${message}`;
        
        this.elements.logContainer.appendChild(entry);
        
        // Auto-scroll to bottom
        this.elements.logContainer.scrollTop = this.elements.logContainer.scrollHeight;

        // Add to state log
        const currentLog = this.state.get('pipeline.log') || [];
        currentLog.push({
            text: message,
            level,
            timestamp: new Date().toISOString()
        });
        this.state.set('pipeline.log', currentLog);
    }

    updateLogDisplay(logEntries) {
        if (!this.elements.logContainer) return;

        this.elements.logContainer.innerHTML = '';
        
        logEntries.forEach(entry => {
            const logElement = document.createElement('div');
            logElement.className = `log-entry text-gray-300 ${this.getLogLevelClass(entry.level || 'info')}`;
            
            // Format timestamp if available
            const timestamp = entry.timestamp ? 
                new Date(entry.timestamp).toLocaleTimeString() : 
                new Date().toLocaleTimeString();
            
            logElement.textContent = `${timestamp} - ${entry.text}`;
            this.elements.logContainer.appendChild(logElement);
        });

        // Auto-scroll to bottom
        this.elements.logContainer.scrollTop = this.elements.logContainer.scrollHeight;
    }

    getLogLevelClass(level) {
        const levelClasses = {
            error: 'text-red-400',
            warning: 'text-yellow-400',
            success: 'text-green-400',
            info: 'text-gray-300'
        };
        return levelClasses[level] || levelClasses.info;
    }

    handleError(message, error) {
        console.error(`[Pipeline] ${message}:`, error);
        this.notifications.error(`${message}: ${error.message}`);
        this.addLogEntry(`❌ ${message}: ${error.message}`, 'error');
        
        this.state.batch({
            'pipeline.status': 'failed',
            'pipeline.currentJob': null
        });
        
        this.stopPolling();
        this.isRunning = false;
        this.updateButtonState(false);
        this.hideProgress();
    }

    // Public API methods
    getPipelineStatus() {
        return this.state.get('pipeline.status');
    }

    getCurrentJob() {
        return this.state.get('pipeline.currentJob');
    }

    isReportAvailable(type) {
        const reports = this.state.get('pipeline.reports');
        return !!(reports && reports[type]);
    }

    getReportFilename(type) {
        const reports = this.state.get('pipeline.reports');
        return reports ? reports[type] : null;
    }

    // Manual status refresh
    async refreshStatus() {
        const currentJob = this.getCurrentJob();
        if (currentJob && this.getPipelineStatus() === 'running') {
            await this.checkStatus(currentJob);
        }
    }

    // Stop pipeline (if supported by backend)
    async stopPipeline() {
        const currentJob = this.getCurrentJob();
        if (!currentJob) {
            this.notifications.warning('No pipeline is currently running');
            return;
        }

        try {
            // This would require backend support
            const response = await this.utils.request(`/api/stop-pipeline/${currentJob}`, {
                method: 'POST'
            });

            if (response.ok) {
                this.notifications.success('Pipeline stop requested');
                this.stopPolling();
                this.isRunning = false;
                this.updateButtonState(false);
                this.state.set('pipeline.status', 'stopped');
            } else {
                throw new Error('Failed to stop pipeline');
            }
        } catch (error) {
            console.error('[Pipeline] Stop failed:', error);
            this.notifications.error('Failed to stop pipeline');
        }
    }

    // Export logs
    exportLogs() {
        const logs = this.state.get('pipeline.log') || [];
        if (logs.length === 0) {
            this.notifications.warning('No logs to export');
            return;
        }

        try {
            const logText = logs.map(entry => {
                const timestamp = entry.timestamp ? 
                    new Date(entry.timestamp).toISOString() : 
                    new Date().toISOString();
                return `[${timestamp}] ${entry.level.toUpperCase()}: ${entry.text}`;
            }).join('\n');

            const filename = `pipeline_logs_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`;
            this.utils.downloadFile(logText, filename, 'text/plain');
            
            this.notifications.success('Logs exported successfully');
        } catch (error) {
            console.error('[Pipeline] Export logs failed:', error);
            this.notifications.error('Failed to export logs');
        }
    }

    // Clear all pipeline data
    clearData() {
        this.stopPolling();
        this.isRunning = false;
        
        this.state.batch({
            'pipeline.status': 'idle',
            'pipeline.currentJob': null,
            'pipeline.log': [],
            'pipeline.reports.opportunities': null,
            'pipeline.reports.matchmaking': null
        });

        this.clearLog();
        this.updateButtonState(false);
        this.hideProgress();
        
        this.notifications.info('Pipeline data cleared');
    }

    // Get pipeline statistics
    getStatistics() {
        const logs = this.state.get('pipeline.log') || [];
        const reports = this.state.get('pipeline.reports');
        
        return {
            totalLogEntries: logs.length,
            errorCount: logs.filter(entry => entry.level === 'error').length,
            warningCount: logs.filter(entry => entry.level === 'warning').length,
            hasOpportunityReport: !!(reports && reports.opportunities),
            hasMatchmakingReport: !!(reports && reports.matchmaking),
            status: this.getPipelineStatus(),
            isRunning: this.isRunning,
            currentJob: this.getCurrentJob()
        };
    }

    // Cleanup
    destroy() {
        this.stopPolling();
        
        // Remove event listeners
        if (this.elements.runButton) {
            this.elements.runButton.removeEventListener('click', this.runPipeline);
        }

        // Clear state
        this.clearData();
        
        // Clear references
        this.elements = {};
        this.state = null;
        this.notifications = null;
        this.utils = null;
    }
}
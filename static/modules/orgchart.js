// modules/orgChart.js - Org Chart processing management
export class OrgChartManager {
    constructor({ state, notifications, utils }) {
        this.state = state;
        this.notifications = notifications;
        this.utils = utils;
        
        this.elements = {};
        this.isProcessing = false;
        this.dotsInterval = null;
        
        // Bind methods
        this.handleFileSelect = this.handleFileSelect.bind(this);
        this.handleFileDrop = this.handleFileDrop.bind(this);
        this.handleDragOver = this.handleDragOver.bind(this);
        this.handleDragLeave = this.handleDragLeave.bind(this);
        this.parseFile = this.parseFile.bind(this);
        this.downloadReport = this.downloadReport.bind(this);
    }

    async init() {
        try {
            this.cacheElements();
            this.bindEvents();
            this.setupStateSubscriptions();
            
            console.log('[OrgChart] Manager initialized');
        } catch (error) {
            console.error('[OrgChart] Initialization failed:', error);
            throw error;
        }
    }

    cacheElements() {
        this.elements = {
            fileUpload: document.getElementById('fileUpload'),
            pdfUploadInput: document.getElementById('pdfUpload'),
            uploadSuccess: document.getElementById('uploadSuccess'),
            fileName: document.getElementById('fileName'),
            parseButton: document.getElementById('parseBtn'),
            parsingSpinner: document.getElementById('parsingSpinner'),
            downloadSection: document.getElementById('downloadSection'),
            downloadButton: document.getElementById('downloadReportBtn'),
            dotPulse: document.getElementById('dotPulse')
        };

        // Validate required elements
        const required = ['fileUpload', 'pdfUploadInput'];
        const missing = required.filter(key => !this.elements[key]);
        
        if (missing.length > 0) {
            console.warn(`[OrgChart] Some elements missing: ${missing.join(', ')}`);
        }
    }

    bindEvents() {
        if (this.elements.fileUpload) {
            this.elements.fileUpload.addEventListener('click', () => {
                if (this.elements.pdfUploadInput) {
                    this.elements.pdfUploadInput.click();
                }
            });

            // Drag and drop
            this.elements.fileUpload.addEventListener('dragover', this.handleDragOver);
            this.elements.fileUpload.addEventListener('dragleave', this.handleDragLeave);
            this.elements.fileUpload.addEventListener('drop', this.handleFileDrop);
        }

        if (this.elements.pdfUploadInput) {
            this.elements.pdfUploadInput.addEventListener('change', this.handleFileSelect);
        }

        if (this.elements.parseButton) {
            this.elements.parseButton.addEventListener('click', this.parseFile);
        }

        if (this.elements.downloadButton) {
            this.elements.downloadButton.addEventListener('click', this.downloadReport);
        }
    }

    setupStateSubscriptions() {
        this.state.subscribe('orgChart', (orgChartState) => {
            this.updateUI(orgChartState);
        });
    }

    handleDragOver(e) {
        e.preventDefault();
        if (this.elements.fileUpload) {
            this.elements.fileUpload.classList.add('dragover');
        }
    }

    handleDragLeave(e) {
        // Only remove dragover if we're actually leaving the drop zone
        if (!this.elements.fileUpload?.contains(e.relatedTarget)) {
            this.elements.fileUpload?.classList.remove('dragover');
        }
    }

    handleFileDrop(e) {
        e.preventDefault();
        this.elements.fileUpload?.classList.remove('dragover');
        
        const files = e.dataTransfer?.files;
        if (files && files.length > 0) {
            this.processFile(files[0]);
        }
    }

    handleFileSelect(e) {
        const files = e.target?.files;
        if (files && files.length > 0) {
            this.processFile(files[0]);
        }
    }

    processFile(file) {
        if (!this.validateFile(file)) {
            return;
        }

        try {
            // Update file input
            if (this.elements.pdfUploadInput) {
                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);
                this.elements.pdfUploadInput.files = dataTransfer.files;
            }

            // Update UI
            this.showFileSelected(file.name);
            
            // Update state
            this.state.set('orgChart.lastProcessedFile', {
                name: file.name,
                size: file.size,
                type: file.type,
                lastModified: file.lastModified
            });

        } catch (error) {
            console.error('[OrgChart] File processing error:', error);
            this.notifications.error('Failed to process file');
        }
    }

    validateFile(file) {
        if (!file) {
            this.notifications.error('No file provided');
            return false;
        }

        if (file.type !== 'application/pdf') {
            this.notifications.error('Please select a PDF file');
            return false;
        }

        // Check file size (e.g., 50MB limit)
        const maxSize = 50 * 1024 * 1024;
        if (file.size > maxSize) {
            this.notifications.error('File is too large. Maximum size is 50MB');
            return false;
        }

        return true;
    }

    showFileSelected(fileName) {
        if (this.elements.fileName) {
            this.elements.fileName.textContent = `File '${fileName}' ready.`;
        }

        // Show success section and parse button
        this.elements.uploadSuccess?.classList.remove('hidden');
        this.elements.parseButton?.classList.remove('hidden');
        
        // Hide file upload area
        this.elements.fileUpload?.classList.add('hidden');
    }

    async parseFile() {
        if (!this.elements.pdfUploadInput?.files?.length) {
            this.notifications.warning('Please select a file first');
            return;
        }

        if (this.isProcessing) {
            this.notifications.warning('File is already being processed');
            return;
        }

        const file = this.elements.pdfUploadInput.files[0];
        
        try {
            this.isProcessing = true;
            this.showProcessing();
            
            // Update state
            this.state.set('orgChart.processing', true);

            const formData = new FormData();
            formData.append('file', file);

            const response = await this.utils.request('/api/parse-org-chart', {
                method: 'POST',
                body: formData,
                headers: {} // Remove Content-Type header for FormData
            });

            const result = await response.json();

            if (result.success) {
                this.handleParsingSuccess(result.filename);
            } else {
                throw new Error(result.error || 'Unknown parsing error');
            }

        } catch (error) {
            console.error('[OrgChart] Parsing failed:', error);
            this.handleParsingError(error);
        } finally {
            this.isProcessing = false;
            this.hideProcessing();
            this.state.set('orgChart.processing', false);
        }
    }

    handleParsingSuccess(filename) {
        this.notifications.success('Org chart processed successfully');
        
        // Update state
        this.state.set('orgChart.reportUrl', filename);
        
        // Show download section
        this.elements.downloadSection?.classList.remove('hidden');
        
        // Set up download button
        if (this.elements.downloadButton) {
            this.elements.downloadButton.onclick = () => this.downloadReport(filename);
        }
    }

    handleParsingError(error) {
        this.notifications.error(`Parsing failed: ${error.message}`);
        
        // Show parse button again for retry
        this.elements.parseButton?.classList.remove('hidden');
        
        // Clear state
        this.state.set('orgChart.reportUrl', null);
    }

    downloadReport(filename = null) {
        const reportUrl = filename || this.state.get('orgChart.reportUrl');
        
        if (!reportUrl) {
            this.notifications.warning('No report available for download');
            return;
        }

        try {
            window.location.href = `/download/${encodeURIComponent(reportUrl)}`;
            this.notifications.success('Downloading org chart report');
        } catch (error) {
            console.error('[OrgChart] Download failed:', error);
            this.notifications.error('Failed to download report');
        }
    }

    showProcessing() {
        this.elements.parseButton?.classList.add('hidden');
        this.elements.parsingSpinner?.classList.remove('hidden');
        this.elements.downloadSection?.classList.add('hidden');
        
        this.startDots();
    }

    hideProcessing() {
        this.elements.parsingSpinner?.classList.add('hidden');
        this.stopDots();
    }

    startDots() {
        if (!this.elements.dotPulse) return;
        
        let count = 0;
        this.dotsInterval = setInterval(() => {
            count = (count + 1) % 4;
            this.elements.dotPulse.textContent = '.'.repeat(count);
        }, 500);
    }

    stopDots() {
        if (this.dotsInterval) {
            clearInterval(this.dotsInterval);
            this.dotsInterval = null;
        }
    }

    updateUI(orgChartState) {
        // Update processing state
        if (orgChartState.processing) {
            this.showProcessing();
        } else {
            this.hideProcessing();
        }

        // Update download availability
        if (orgChartState.reportUrl) {
            this.elements.downloadSection?.classList.remove('hidden');
        } else {
            this.elements.downloadSection?.classList.add('hidden');
        }
    }

    // Reset the form to initial state
    resetForm() {
        // Clear file input
        if (this.elements.pdfUploadInput) {
            this.elements.pdfUploadInput.value = '';
        }

        // Reset UI elements
        this.elements.uploadSuccess?.classList.add('hidden');
        this.elements.parseButton?.classList.add('hidden');
        this.elements.fileUpload?.classList.remove('hidden');
        this.elements.downloadSection?.classList.add('hidden');
        
        // Clear text content
        if (this.elements.fileName) {
            this.elements.fileName.textContent = '';
        }

        // Reset state
        this.state.batch({
            'orgChart.processing': false,
            'orgChart.lastProcessedFile': null,
            'orgChart.reportUrl': null
        });

        this.notifications.info('Form reset');
    }

    // Public API methods
    isProcessingFile() {
        return this.isProcessing;
    }

    getLastProcessedFile() {
        return this.state.get('orgChart.lastProcessedFile');
    }

    hasReport() {
        return !!this.state.get('orgChart.reportUrl');
    }

    getReportUrl() {
        return this.state.get('orgChart.reportUrl');
    }

    // File validation utilities
    getSupportedFileTypes() {
        return ['application/pdf'];
    }

    getMaxFileSize() {
        return 50 * 1024 * 1024; // 50MB
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // Statistics
    getStatistics() {
        const lastFile = this.getLastProcessedFile();
        
        return {
            isProcessing: this.isProcessingFile(),
            hasReport: this.hasReport(),
            lastProcessedFile: lastFile ? {
                name: lastFile.name,
                size: this.formatFileSize(lastFile.size),
                type: lastFile.type,
                processedAt: new Date(lastFile.lastModified).toLocaleString()
            } : null,
            reportAvailable: this.hasReport()
        };
    }

    // Cleanup
    destroy() {
        this.stopDots();
        
        // Remove event listeners
        if (this.elements.fileUpload) {
            this.elements.fileUpload.removeEventListener('dragover', this.handleDragOver);
            this.elements.fileUpload.removeEventListener('dragleave', this.handleDragLeave);
            this.elements.fileUpload.removeEventListener('drop', this.handleFileDrop);
        }

        if (this.elements.pdfUploadInput) {
            this.elements.pdfUploadInput.removeEventListener('change', this.handleFileSelect);
        }

        if (this.elements.parseButton) {
            this.elements.parseButton.removeEventListener('click', this.parseFile);
        }

        if (this.elements.downloadButton) {
            this.elements.downloadButton.removeEventListener('click', this.downloadReport);
        }

        // Clear references
        this.elements = {};
        this.state = null;
        this.notifications = null;
        this.utils = null;
    }
}
// document.addEventListener('DOMContentLoaded', () => {
document.addEventListener('DOMContentLoaded', () => {
    let dotsInterval;
    function startDots() {
        const dots = document.getElementById('dotPulse');
        if (!dots) return;
        let count = 0;
        dotsInterval = setInterval(() => {
            count = (count + 1) % 4;
            dots.textContent = '.'.repeat(count);
        }, 500);
    }
    function stopDots() {
        clearInterval(dotsInterval);
    }

    // --- Universal Tab Switching Logic ---
    const tabs = document.querySelectorAll('.tab-button');
    const projectTabId = 'tab5';
    tabs.forEach(button => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            button.classList.add('active');
            const tabId = button.getAttribute('data-tab');
            document.getElementById(tabId).classList.add('active');
            if (tabId === projectTabId && pmFilter.options.length <= 1) {
                populatePmFilter();
            }
        });
    });

    // --- Tab 1: Opportunity Pipeline Logic (CORRECTED) ---
    const runPipelineBtn = document.getElementById('runPipelineBtn');
    if (runPipelineBtn) {
        const progressEl = document.getElementById('pipelineProgress');
        const logContainerEl = document.getElementById('logContainer');
        const oppsBtn = document.getElementById('downloadOppsBtn');
        const matchBtn = document.getElementById('downloadMatchBtn');

        async function checkPipelineStatus(jobId) {
            try {
                const response = await fetch(`/api/pipeline-status/${jobId}`);
                if (!response.ok) {
                    logContainerEl.innerHTML += '<div class="text-red-500">Error: Could not get pipeline status.</div>';
                    return;
                }
                const data = await response.json();

                if (data && data.log && Array.isArray(data.log)) {
                    logContainerEl.innerHTML = '';
                    data.log.forEach(entry => {
                        const logEntry = document.createElement('div');
                        logEntry.className = 'log-entry text-gray-300';
                        logEntry.textContent = entry.text;
                        logContainerEl.appendChild(logEntry);
                    });
                }

                if (data.status === 'completed' || data.status === 'failed') {
                    runPipelineBtn.disabled = false;
                    if (data.opps_report_filename) {
                        oppsBtn.onclick = () => window.location.href = `/download/${data.opps_report_filename}`;
                        oppsBtn.disabled = false;
                    }
                    if (data.match_report_filename) {
                        matchBtn.onclick = () => window.location.href = `/download/${data.match_report_filename}`;
                        matchBtn.disabled = false;
                    }
                    if (data.status === 'failed') {
                        logContainerEl.innerHTML += '<div class="text-red-500">Pipeline failed. Check server logs for details.</div>';
                    }
                } else {
                    setTimeout(() => checkPipelineStatus(jobId), 3000);
                }
            } catch (error) {
                logContainerEl.innerHTML += `<div class="text-red-500">Error: ${error}</div>`;
                runPipelineBtn.disabled = false;
            }
        }

        runPipelineBtn.addEventListener('click', async () => {
            progressEl.classList.remove('hidden');
            logContainerEl.innerHTML = '<div class="text-gray-400">🚀 Starting pipeline...</div>';
            runPipelineBtn.disabled = true;
            oppsBtn.disabled = true;
            matchBtn.disabled = true;

            try {
                const response = await fetch('/api/run-pipeline', { method: 'POST' });
                const result = await response.json();
                if (result.job_id) {
                    checkPipelineStatus(result.job_id);
                } else {
                    logContainerEl.innerHTML = '<div class="text-red-500">Error: Could not start pipeline job.</div>';
                    runPipelineBtn.disabled = false;
                }
            } catch (error) {
                logContainerEl.innerHTML = `<div class="text-red-500">${error}</div>`;
                runPipelineBtn.disabled = false;
            }
        });
    }

    // --- Tab 2: Org Chart Parser Logic ---
    const fileUploadEl = document.getElementById('fileUpload');
    if (fileUploadEl) {
        const pdfUploadInput = document.getElementById('pdfUpload');
        const uploadSuccessEl = document.getElementById('uploadSuccess');
        const fileNameEl = document.getElementById('fileName');
        const parseBtn = document.getElementById('parseBtn');
        const parsingSpinner = document.getElementById('parsingSpinner');
        const downloadSection = document.getElementById('downloadSection');
        const downloadReportBtn = document.getElementById('downloadReportBtn');
        const handleFile = (file) => {
            if (file && file.type === 'application/pdf') {
                const dt = new DataTransfer();
                dt.items.add(file);
                pdfUploadInput.files = dt.files;
                fileNameEl.textContent = `File '${file.name}' ready.`;
                uploadSuccessEl.classList.remove('hidden');
                parseBtn.classList.remove('hidden');
                fileUploadEl.classList.add('hidden');
            } else { alert('Please select a PDF file.'); }
        };
        fileUploadEl.addEventListener('click', () => pdfUploadInput.click());
        pdfUploadInput.addEventListener('change', (e) => handleFile(e.target.files[0]));
        fileUploadEl.addEventListener('dragover', (e) => { e.preventDefault(); fileUploadEl.classList.add('dragover'); });
        fileUploadEl.addEventListener('dragleave', () => fileUploadEl.classList.remove('dragover'));
        fileUploadEl.addEventListener('drop', (e) => { e.preventDefault(); fileUploadEl.classList.remove('dragover'); handleFile(e.dataTransfer.files[0]); });
        parseBtn.addEventListener('click', async () => {
            if (pdfUploadInput.files.length === 0) return;
            const formData = new FormData();
            formData.append('file', pdfUploadInput.files[0]);
            parseBtn.classList.add('hidden');
            parsingSpinner.classList.remove('hidden');
            startDots();
            downloadSection.classList.add('hidden');
            try {
                const response = await fetch('/api/parse-org-chart', { method: 'POST', body: formData });
                const result = await response.json();
                if (result.success) {
                    downloadReportBtn.onclick = () => window.location.href = `/download/${result.filename}`;
                    downloadSection.classList.remove('hidden');
                } else { throw new Error(result.error || 'Unknown error.'); }
            } catch (error) {
                alert(`Error: ${error.message}`);
                parseBtn.classList.remove('hidden');
            } finally {
                parsingSpinner.classList.add('hidden');
                stopDots();
            }
        });
    }

    // --- Tab 5: Project Reporting Logic ---
    const projectListMenu = document.getElementById('project-list-menu');
    const projectDetailView = document.getElementById('project-detail-view');
    const statusFilter = document.getElementById('statusFilter');
    const pmFilter = document.getElementById('pmFilter');
    let allProjectsData = [];
    let currentlySelectedProject = null;

    async function populatePmFilter() {
        try {
            const response = await fetch('/api/pms');
            const pmNames = await response.json();
            pmFilter.innerHTML = '<option value="All">Select a Manager...</option>';
            pmNames.forEach(name => {
                if (name) {
                    const option = document.createElement('option');
                    option.value = name;
                    option.textContent = name;
                    pmFilter.appendChild(option);
                }
            });
        } catch (error) { console.error("Could not load PM filter:", error); }
    }

    async function loadProjectsForPm(pmName) {
        if (!pmName || pmName === 'All') {
            allProjectsData = [];
            currentlySelectedProject = null;
            processAndRender();
            return;
        }
        projectListMenu.innerHTML = '<p class="text-gray-500">Loading projects...</p>';
        try {
            const response = await fetch(`/api/projects?pm=${encodeURIComponent(pmName)}`);
            allProjectsData = await response.json();
            currentlySelectedProject = null;
            processAndRender();
        } catch (error) { projectListMenu.innerHTML = `<p class="text-red-500">Error: ${error}</p>`; }
    }

    function processAndRender() {
        if (!pmFilter || pmFilter.value === 'All') {
            projectListMenu.innerHTML = '<p class="text-gray-500 text-sm">Select a PM to see projects.</p>';
            projectDetailView.innerHTML = '<div class="bg-white rounded-xl shadow-md p-6 border"><p class="text-gray-500 text-center">Please select a Project Manager to begin.</p></div>';
            return;
        }
        const statusFilterValue = statusFilter.value;
        let processedData = allProjectsData.filter(p => statusFilterValue === 'All' || p.status === statusFilterValue);
        renderProjectList(processedData);
        let projectToDisplay = processedData.find(p => p.projectName === currentlySelectedProject) || processedData[0];
        if (projectToDisplay) {
            const currentDetail = projectDetailView.querySelector(`[data-project-name="${projectToDisplay.projectName}"]`);
            if (!currentDetail) {
                renderProjectDetail(projectToDisplay.projectName);
            }
        } else {
            projectDetailView.innerHTML = '<div class="bg-white rounded-xl shadow-md p-6 border"><p class="text-gray-500 text-center">No projects match the current filters.</p></div>';
        }
    }

    function renderProjectList(projects) {
        projectListMenu.innerHTML = '';
        if (projects.length === 0) {
            projectListMenu.innerHTML = '<p class="text-gray-500 text-sm">No projects match filters.</p>';
            return;
        }
        projects.forEach(project => {
            const menuItem = document.createElement('button');
            menuItem.className = 'project-card-item block w-full text-left p-2 rounded-md text-gray-700 hover:bg-gray-200 focus:outline-none';
            menuItem.textContent = project.projectName;
            menuItem.dataset.projectName = project.projectName;
            if (project.projectName === currentlySelectedProject) {
                menuItem.classList.add('bg-blue-100', 'font-semibold');
            }
            projectListMenu.appendChild(menuItem);
        });
    }

    async function renderProjectDetail(projectName) {
        if (projectName === currentlySelectedProject) return;
        currentlySelectedProject = projectName;
        const project = allProjectsData.find(p => p.projectName === projectName);
        if (!project) {
            projectDetailView.innerHTML = '<div class="bg-white rounded-xl shadow-md p-6 border"><p class="text-gray-500 text-center">Select a project.</p></div>';
            return;
        }
        projectDetailView.innerHTML = createProjectCardHTML(project);
        await fetchAndPopulateUpdate(projectName);
        document.querySelectorAll('#project-list-menu button').forEach(button => {
            button.classList.toggle('bg-blue-100', button.dataset.projectName === projectName);
            button.classList.toggle('font-semibold', button.dataset.projectName === projectName);
        });
    }

    async function fetchAndPopulateUpdate(projectName) {
        const card = projectDetailView;
        if (!card || !projectName) return;
        const month = card.querySelector('.month-select').value;
        const year = card.querySelector('.year-input').value;
        const managerUpdateBox = card.querySelector('.manager-update-textarea');
        const aiSummaryBox = card.querySelector('.ai-summary-box');
        managerUpdateBox.value = '';
        aiSummaryBox.textContent = '';
        try {
            const response = await fetch(`/api/get_update?projectName=${encodeURIComponent(projectName)}&month=${month}&year=${year}`);
            const updateData = await response.json();
            managerUpdateBox.value = updateData.managerUpdate || '';
            aiSummaryBox.textContent = updateData.aiSummary || '';
        } catch (error) { console.error("Error fetching update:", error); }
    }

    if (pmFilter) {
        pmFilter.addEventListener('change', () => {
            currentlySelectedProject = null;
            loadProjectsForPm(pmFilter.value);
        });
    }
    if (statusFilter) {
        statusFilter.addEventListener('change', processAndRender);
    }
    if (projectListMenu) {
        projectListMenu.addEventListener('click', (event) => {
            if (event.target.tagName === 'BUTTON') renderProjectDetail(event.target.dataset.projectName);
        });
    }
    if (projectDetailView) {
        projectDetailView.addEventListener('change', event => {
            if (event.target.classList.contains('month-select') || event.target.classList.contains('year-input')) {
                fetchAndPopulateUpdate(currentlySelectedProject);
            }
        });
        projectDetailView.addEventListener('click', async function (event) {
            if (event.target.classList.contains('generate-save-btn')) {
                const button = event.target;
                
                const card = button.closest('.bg-gray-50');
                if (!card) {
                    console.error("Could not find the project card container!");
                    return;
                }

                const projectName = button.dataset.projectName;
                const description = button.dataset.description;
                const month = card.querySelector('.month-select').value;
                const year = card.querySelector('.year-input').value;
                const managerUpdate = card.querySelector('.manager-update-textarea').value;
                const aiSummaryBox = card.querySelector('.ai-summary-box');
                if (!managerUpdate.trim()) { alert('Please enter an update.'); return; }
                button.disabled = true;
                button.textContent = 'Processing...';
                aiSummaryBox.textContent = 'Generating AI summary...';
                try {
                    const response = await fetch('/api/update_project', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ projectName, description, month, year, managerUpdate })
                    });
                    const result = await response.json();
                    if (result.success) {
                        aiSummaryBox.textContent = result.aiSummary;
                    } else { throw new Error(result.error || 'Unknown error'); }
                } catch (error) {
                    aiSummaryBox.textContent = `Error: ${error.message}`;
                } finally {
                    button.disabled = false;
                    button.textContent = 'Generate & Save';
                }
            }
        });
    }

    function createProjectCardHTML(project) {
    const now = new Date();
    const currentYear = now.getFullYear();
    const currentMonth = now.toLocaleString('default', { month: 'long' });
    const months = ["January","February","March","April","May","June","July","August","September","October","November","December"];

    const optionsHtml = months
        .map(m => '<option ' + (m === currentMonth ? 'selected' : '') + '>' + m + '</option>')
        .join('');

    const statusBadge =
        (project.status === 'Active')
            ? 'bg-green-100 text-green-800'
            : 'bg-gray-200 text-gray-800';

    return `
        <div class="bg-white rounded-xl shadow-md p-6 border border-gray-200">
            <div class="flex justify-between items-start">
                <h3 class="text-xl font-bold text-gray-800" data-project-name="${project.projectName}">${project.projectName}</h3>
                <span class="px-3 py-1 text-sm font-medium rounded-full ${statusBadge}">${project.status}</span>
            </div>
            <div class="text-sm text-gray-500 mt-2 border-b pb-4 mb-4">
                <span><strong>PI:</strong> ${project.pi || 'N/A'}</span> |
                <span><strong>PM:</strong> ${project.pm || 'N/A'}</span> |
                <span><strong>End Date:</strong> ${project.endDate || 'N/A'}</span>
            </div>
            <details class="mb-4" open>
                <summary class="cursor-pointer font-medium text-blue-600 hover:underline">Project Description</summary>
                <p class="text-gray-600 mt-2 p-3 bg-gray-50 rounded-md">${project.description || 'No description available.'}</p>
            </details>
            <div class="bg-gray-50 p-4 rounded-lg">
                <h4 class="font-semibold text-gray-700 mb-3">Submit Monthly Update</h4>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-3">
                    <div>
                        <label class="block text-sm font-medium text-gray-600">Month</label>
                        <select class="month-select mt-1 block w-full p-2 border border-gray-300 rounded-md">
                            ${optionsHtml}
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-600">Year</label>
                        <input type="number" class="year-input mt-1 block w-full p-2 border border-gray-300 rounded-md" value="${currentYear}">
                    </div>
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-600">Your Update</label>
                    <textarea class="manager-update-textarea mt-1 block w-full p-2 border border-gray-300 rounded-md" rows="4" placeholder="Enter your monthly progress update here..."></textarea>
                </div>
                <div class="mt-4">
                    <label class="block text-sm font-medium text-gray-600">AI-Generated Quarterly Summary</label>
                    <div class="ai-summary-box mt-1 block w-full p-2 bg-white border border-gray-300 rounded-md min-h-[100px]"></div>
                </div>
                <button
                    class="generate-save-btn mt-4 w-full md:w-auto float-right bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg transition duration-200"
                    data-project-name="${project.projectName}"
                    data-description="${project.description}">
                    Generate & Save
                </button>
            </div>
        </div>
    `;
}

    populatePmFilter();

    try {
        if (!window._boeInitOnce) {
            window._boeInitOnce = true;
            }
    } catch (e) {
        console.error('[BoE] init failed:', e);
    }
    populatePmFilter();
    initializeBoeGenerator();   
});   

// --- BoE Generator Logic ---
let LABOR_RATES = {};
let logoBase64 = '';
const OVERHEAD_RATE = 0.17;
const GNA_RATE = 0.10;
const FEE_RATE = 0.07;

function formatCurrency(value) {
    const num = parseFloat(value);
    if (isNaN(num)) return '$0.00';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
}

function initializeBoeGenerator() {
    console.log('[BoE] init start');

    fetch('/static/images/wbi-logo-horz.png')
        .then(response => { if (!response.ok) throw new Error('Logo not found'); return response.blob(); })
        .then(blob => {
            const reader = new FileReader();
            reader.onloadend = () => { logoBase64 = reader.result; };
            reader.readAsDataURL(blob);
        }).catch(err => console.error("Could not pre-load logo for PDF:", err));

    console.log('[BoE] fetching /api/rates …');
    
    fetch('/api/rates')
        .then(response => { if (!response.ok) { throw new Error('Network response was not ok'); } return response.json(); })
        .then(data => {
            LABOR_RATES = data;
            populatePersonnelCheckboxes();
            populateLaborTable([]);
            const estimateBtn = document.getElementById('ai-estimate-btn');
            if(estimateBtn) estimateBtn.disabled = false;
        })
        .catch(error => {
            console.error('Error fetching labor rates:', error);
            const personnelCheckboxes = document.getElementById('personnel-checkboxes');
            if(personnelCheckboxes) personnelCheckboxes.innerHTML = `<p class="col-span-full text-red-500">Error: Could not load labor rates.</p>`;
        });

    document.querySelectorAll('.boe-tab-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            document.querySelectorAll('.boe-tab-btn, .boe-tab-content').forEach(el => el.classList.remove('active'));
            const tabButton = e.currentTarget; 
            if (tabButton && tabButton instanceof HTMLElement) {
                tabButton.classList.add('active');
                const tabId = tabButton.dataset.boetab;
                if(tabId) {
                    const tabContent = document.getElementById(tabId);
                    if (tabContent) tabContent.classList.add('active');
                }
            }
        });
    });

    const estimateBtn = document.getElementById('ai-estimate-btn');
    if(estimateBtn) estimateBtn.disabled = true;
    
    const laborTable = document.getElementById('labor-table');
    if (laborTable) {
        laborTable.addEventListener('input', calculateTotalLaborHours);
        const laborTableBody = laborTable.querySelector('tbody');
        if (laborTableBody && typeof Sortable !== 'undefined') {
             new Sortable(laborTableBody, { animation: 150, ghostClass: 'sortable-ghost' });
        }
    }
    
    setupDynamicTable('materials-table', 'add-material-btn', getMaterialRowHTML, calculateMaterials);
    setupDynamicTable('travel-table', 'add-travel-btn', getTravelRowHTML, calculateTravel);
    setupDynamicTable('subcontracts-table', 'add-subcontract-btn', getSubcontractRowHTML, ()=>{});
    
    const addTaskBtn = document.getElementById('add-task-btn');
    if(addTaskBtn) addTaskBtn.addEventListener('click', () => { addLaborRow(); calculateTotalLaborHours(); });
    
    const generateBtn = document.getElementById('generate-btn');
    if(generateBtn) generateBtn.addEventListener('click', handleGenerateBoe);
    
    if(estimateBtn) estimateBtn.addEventListener('click', handleAIEstimate);
    
    const dropZone = document.getElementById('drop-zone');
    if (dropZone) {
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault(); 
            dropZone.classList.add('dragover');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault(); 
            dropZone.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file) {
                handleScopeFile(file);
            }
        });
    }
}

function handleScopeFile(file) {
    const dropZoneText = document.getElementById('drop-zone-text');
    const scopeTextArea = document.getElementById('scope');
    dropZoneText.textContent = `Processing: ${file.name}...`;

    try {
        const reader = new FileReader();
        if (file.type === 'text/plain') {
            reader.onload = (event) => { scopeTextArea.value = event.target.result; dropZoneText.textContent = `Loaded: ${file.name}`; };
            reader.readAsText(file);
        } else if (file.name.endsWith('.docx')) {
            reader.onload = (event) => {
                mammoth.extractRawText({ arrayBuffer: event.target.result })
                    .then(result => { scopeTextArea.value = result.value; dropZoneText.textContent = `Loaded: ${file.name}`; })
                    .catch(err => { throw err; });
            };
            reader.readAsArrayBuffer(file);
        } else if (file.type === 'application/pdf') {
            pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js`;
            reader.onload = (event) => {
                const loadingTask = pdfjsLib.getDocument({ data: event.target.result });
                loadingTask.promise.then(pdf => {
                    const pagePromises = Array.from({ length: pdf.numPages }, (_, i) => pdf.getPage(i + 1).then(page => page.getTextContent()));
                    Promise.all(pagePromises).then(textContents => {
                        let fullText = textContents.map(tc => tc.items.map(item => item.str).join(' ')).join('\n');
                        scopeTextArea.value = fullText;
                        dropZoneText.textContent = `Loaded: ${file.name}`;
                    }).catch(err => { throw err; });
                }).catch(err => { throw err; });
            };
            reader.readAsArrayBuffer(file);
        } else if (file.name.endsWith('.xlsx')) {
            reader.onload = (event) => {
                const wb = XLSX.read(event.target.result, { type: 'binary' });
                let txt = '';
                wb.SheetNames.forEach(sheetName => { txt += XLSX.utils.sheet_to_csv(wb.Sheets[sheetName]) + '\n'; });
                scopeTextArea.value = txt;
                dropZoneText.textContent = `Loaded: ${file.name}`;
            };
            reader.readAsBinaryString(file);
        } else {
            throw new Error(`Unsupported file type: ${file.type || 'unknown'}.`);
        }
        reader.onerror = () => { throw new Error("Error reading file buffer."); };
    } catch (error) {
        console.error("File Ingest Error:", error);
        alert(`Failed to process file. Check console (F12) for details.`);
        dropZoneText.textContent = "Drag & drop or paste scope";
    }
}

function populatePersonnelCheckboxes() {
    const container = document.getElementById('personnel-checkboxes');
    if (!Object.keys(LABOR_RATES).length) {
        container.innerHTML = `<p class="col-span-full text-gray-500">Could not load personnel.</p>`;
        return;
    }
    container.innerHTML = Object.keys(LABOR_RATES).map(role => {
        const safeId = "personnel_" + role.replace(/[^a-zA-Z0-9]/g, '');
        return `<div><input type="checkbox" id="${safeId}" value="${role}" class="h-4 w-4 rounded border-gray-300 text-yellow-600 focus:ring-yellow-500" checked><label for="${safeId}" class="ml-2 text-sm">${role}</label></div>`;
    }).join('');
}

function populateLaborTable(work_plan = []) {
    const table = document.getElementById('labor-table'), thead = table.querySelector('thead'), tbody = table.querySelector('tbody');
    const availableRoles = Object.keys(LABOR_RATES);
    if (!thead.querySelector('tr')) {
        thead.innerHTML = `<tr><th class="p-2 text-left w-2/5">Work Breakdown Structure Element</th>${availableRoles.map(role => `<th class="p-2 text-sm">${role}</th>`).join('')}<th></th></tr>`;
    }
    tbody.innerHTML = '';
    if (!work_plan || work_plan.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${availableRoles.length + 2}" class="p-4 text-center text-gray-500">No labor tasks defined.</td></tr>`;
    } else {
        work_plan.forEach(item => addLaborRow(item.task, item.hours));
    }
    calculateTotalLaborHours();
}

function populateMaterialsTable(materials = []) {
    const tbody = document.getElementById('materials-table').querySelector('tbody');
    tbody.innerHTML = '';
    if (!materials || materials.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="p-4 text-center text-gray-500">No materials defined.</td></tr>`;
        return;
    }
    materials.forEach(item => {
        const newRow = document.createElement('tr');
        newRow.innerHTML = getMaterialRowHTML(item.part_number, item.description, item.vendor, item.quantity, item.unit_cost);
        tbody.appendChild(newRow);
    });
    calculateMaterials();
}

function populateTravelTable(travel = []) {
    const tbody = document.getElementById('travel-table').querySelector('tbody');
    tbody.innerHTML = '';
    if (!travel || travel.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="p-4 text-center text-gray-500">No travel defined.</td></tr>`;
        return;
    }
    travel.forEach(item => {
        const newRow = document.createElement('tr');
        newRow.innerHTML = getTravelRowHTML(item.purpose, item.trips, item.travelers, item.days, item.airfare, item.lodging, item.per_diem);
        tbody.appendChild(newRow);
    });
    calculateTravel();
}

function populateSubcontractsTable(subcontracts = []) {
    const tbody = document.getElementById('subcontracts-table').querySelector('tbody');
    tbody.innerHTML = '';
    if (!subcontracts || subcontracts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="p-4 text-center text-gray-500">No subcontracts defined.</td></tr>`;
        return;
    }
    subcontracts.forEach(item => {
        const newRow = document.createElement('tr');
        newRow.innerHTML = getSubcontractRowHTML(item.subcontractor, item.description, item.cost);
        tbody.appendChild(newRow);
    });
}

function addLaborRow(task = '', hours = {}) {
    const tbody = document.getElementById('labor-table').querySelector('tbody');
    const availableRoles = Object.keys(LABOR_RATES);
    if (tbody.querySelector('td[colspan]')) tbody.innerHTML = '';
    const row = document.createElement('tr');
    const hourInputs = availableRoles.map(role => `<td class="table-cell"><input type="number" class="table-input" value="${hours[role] || 0}"></td>`).join('');
    row.innerHTML = `<td class="table-cell"><input type="text" class="table-input text-left" value="${task}"></td>${hourInputs}<td class="table-cell text-center"><button class="delete-btn">X</button></td>`;
    tbody.appendChild(row);
    row.querySelector('.delete-btn').addEventListener('click', () => { row.remove(); calculateTotalLaborHours(); });
}

function setupDynamicTable(tableId, addBtnId, htmlFactory, calcFn) {
    const table = document.getElementById(tableId);
    const addBtn = document.getElementById(addBtnId);
    if (!table || !addBtn) return;
    table.addEventListener('input', calcFn);
    table.addEventListener('click', (e) => {
        if (e.target.classList.contains('delete-btn')) {
            e.target.closest('tr').remove();
            if (calcFn) calcFn();
        }
    });
    addBtn.addEventListener('click', () => {
        const tbody = table.querySelector('tbody');
        if (tbody.querySelector('td[colspan]')) tbody.innerHTML = '';
        const newRow = document.createElement('tr');
        newRow.innerHTML = htmlFactory();
        tbody.appendChild(newRow);
    });
}

const getMaterialRowHTML = (partNum = '', desc = '', vend = '', qty = 1, cost = 0) => `<td class="table-cell"><input type="text" class="table-input text-left" value="${partNum || ''}"></td><td class="table-cell"><input type="text" class="table-input text-left" value="${desc || ''}"></td><td class="table-cell"><input type="text" class="table-input text-left" value="${vend || ''}"></td><td class="table-cell"><input type="number" class="table-input" value="${qty || 1}"></td><td class="table-cell"><input type="number" class="table-input" value="${cost || 0}"></td><td class="p-2 text-right">$0.00</td><td class="table-cell text-center"><button class="delete-btn">X</button></td>`;
const getTravelRowHTML = (purp = '', trips = 1, trav = 1, days = 1, air = 0, lodge = 0, diem = 0) => `<td class="table-cell"><input type="text" class="table-input text-left" value="${purp || ''}"></td><td class="table-cell"><input type="number" class="table-input" value="${trips || 1}"></td><td class="table-cell"><input type="number" class="table-input" value="${trav || 1}"></td><td class="table-cell"><input type="number" class="table-input" value="${days || 1}"></td><td class="table-cell"><input type="number" class="table-input" value="${air || 0}"></td><td class="table-cell"><input type="number" class="table-input" value="${lodge || 0}"></td><td class="table-cell"><input type="number" class="table-input" value="${diem || 0}"></td><td class="p-2 text-right">$0.00</td><td class="table-cell text-center"><button class="delete-btn">X</button></td>`;
const getSubcontractRowHTML = (sub = '', desc = '', cost = 0) => `<td class="table-cell"><input type="text" class="table-input text-left" value="${sub || ''}"></td><td class="table-cell"><input type="text" class="table-input text-left" value="${desc || ''}"></td><td class="table-cell"><input type="number" class="table-input" value="${cost || 0}"></td><td class="table-cell text-center"><button class="delete-btn">X</button></td>`;

function calculateTotalLaborHours() {
    let total = 0;
    document.querySelectorAll('#labor-table tbody input[type="number"]').forEach(input => { total += parseFloat(input.value) || 0; });
    document.getElementById('total-labor-hours').textContent = total.toLocaleString();
}
function calculateMaterials() { document.querySelectorAll('#materials-table tbody tr').forEach(row => { if (row.cells.length > 5) { const i = row.querySelectorAll('input'); row.cells[5].textContent = formatCurrency((parseFloat(i[3].value) || 0) * (parseFloat(i[4].value) || 0)); } }); }
function calculateTravel() { document.querySelectorAll('#travel-table tbody tr').forEach(row => { if (row.cells.length > 7) { const i = row.querySelectorAll('input'); const trips = parseFloat(i[1].value) || 0; const travelers = parseFloat(i[2].value) || 0; const days = parseFloat(i[3].value) || 0; const airfare = parseFloat(i[4].value) || 0; const lodging = parseFloat(i[5].value) || 0; const perDiem = parseFloat(i[6].value) || 0; row.cells[7].textContent = formatCurrency(trips * travelers * (airfare + lodging + (perDiem * days))); } }); }

function calculateAllTotals(projectData) {
    const { work_plan, materials_and_tools, travel, subcontracts } = projectData;
    let laborCost = 0;
    if (work_plan) {
        work_plan.forEach(task => {
            for (const role in task.hours) {
                if (Object.prototype.hasOwnProperty.call(task.hours, role)) {
                    laborCost += (task.hours[role] || 0) * (LABOR_RATES[role] || 0);
                }
            }
        });
    }
    const materialsCost = materials_and_tools ? materials_and_tools.reduce((s, r) => s + ((r.quantity || 0) * (r.unit_cost || 0)), 0) : 0;
    const travelCost = travel ? travel.reduce((s, r) => s + ((r.trips || 0) * (r.travelers || 0) * ((r.airfare || 0) + (r.lodging || 0) + ((r.days || 0) * (r.per_diem || 0)))), 0) : 0;
    const subcontractCost = subcontracts ? subcontracts.reduce((s, r) => s + (r.cost || 0), 0) : 0;
    const totalDirect = laborCost + materialsCost + travelCost + subcontractCost;
    const overhead = laborCost * OVERHEAD_RATE;
    const subtotal = totalDirect + overhead;
    const gna = subtotal * GNA_RATE;
    const totalCost = subtotal + gna;
    const fee = totalCost * FEE_RATE;
    const totalPrice = totalCost + fee;
    return { laborCost, materialsCost, travelCost, subcontractCost, totalDirectCosts: totalDirect, overheadAmount: overhead, subtotal, gnaAmount: gna, totalCost, feeAmount: fee, totalPrice };
}

function updateUIFromData(projectData) {
    if (projectData.project_title !== undefined) { document.getElementById('projectTitle').value = projectData.project_title; }
    if (projectData.start_date !== undefined) { document.getElementById('startDate').value = projectData.start_date; }
    if (projectData.pop !== undefined) { document.getElementById('pop').value = projectData.pop; }
    if (projectData.work_plan) { populateLaborTable(projectData.work_plan); }
    if (projectData.materials_and_tools) { populateMaterialsTable(projectData.materials_and_tools); }
    if (projectData.travel) { populateTravelTable(projectData.travel); }
    if (projectData.subcontracts) { populateSubcontractsTable(projectData.subcontracts); }
}

function getCurrentProjectData() {
    const project_title = document.getElementById('projectTitle').value;
    const start_date = document.getElementById('startDate').value;
    const pop = document.getElementById('pop').value;
    const work_plan = Array.from(document.querySelectorAll('#labor-table tbody tr')).map(row => {
        if (row.querySelector('td[colspan]')) return null;
        const inputs = row.querySelectorAll('input');
        const task = inputs[0].value;
        const hours = {};
        const headers = Array.from(document.querySelectorAll('#labor-table thead th')).map(th => th.textContent.trim()).slice(1, -1);
        headers.forEach((header, index) => {
            hours[header] = parseFloat(inputs[index + 1].value) || 0;
        });
        return { task, hours };
    }).filter(Boolean);
    const materials_and_tools = Array.from(document.querySelectorAll('#materials-table tbody tr')).map(row => {
        const inputs = row.querySelectorAll('input');
        if (!inputs.length || row.querySelector('td[colspan]')) return null;
        return { part_number: inputs[0].value, description: inputs[1].value, vendor: inputs[2].value, quantity: parseFloat(inputs[3].value) || 0, unit_cost: parseFloat(inputs[4].value) || 0 };
    }).filter(Boolean);
    const travel = Array.from(document.querySelectorAll('#travel-table tbody tr')).map(row => {
        const inputs = row.querySelectorAll('input');
        if (!inputs.length || row.querySelector('td[colspan]')) return null;
        return { purpose: inputs[0].value, trips: parseFloat(inputs[1].value) || 0, travelers: parseFloat(inputs[2].value) || 0, days: parseFloat(inputs[3].value) || 0, airfare: parseFloat(inputs[4].value) || 0, lodging: parseFloat(inputs[5].value) || 0, per_diem: parseFloat(inputs[6].value) || 0 };
    }).filter(Boolean);
    const subcontracts = Array.from(document.querySelectorAll('#subcontracts-table tbody tr')).map(row => {
        const inputs = row.querySelectorAll('input');
        if (!inputs.length || row.querySelector('td[colspan]')) return null;
        return { subcontractor: inputs[0].value, description: inputs[1].value, cost: parseFloat(inputs[2].value) || 0 };
    }).filter(Boolean);
    return { project_title, start_date, pop, work_plan, materials_and_tools, travel, subcontracts };
}

async function handleAIEstimate() {
    const btn = document.getElementById('ai-estimate-btn');
    const spinner = document.getElementById('ai-spinner');
    const btnText = document.getElementById('ai-btn-text');

    btn.disabled = true;
    spinner.classList.remove('hidden');
    btnText.textContent = "Estimating...";

    const scope = document.getElementById('scope').value || '';
    const pop = document.getElementById('pop').value || '';
    const personnel = Array.from(document.querySelectorAll('#personnel-checkboxes input:checked'))
        .map(cb => cb.value);

    if (personnel.length === 0) {
        alert("Please select at least one personnel role.");
        btn.disabled = false;
        spinner.classList.add('hidden');
        btnText.textContent = "AI Estimate Full Project";
        return;
    }

    const new_request = `**Scope of Work:** ${scope}
**Period of Performance:** ${pop} months
**Available Personnel:** ${personnel.join(', ')}`;

    const case_history = "";

    const controller = new AbortController();
    const timeoutMs = 30000; // 30s
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    console.log('[AI] Sending /api/estimate …', { timeoutMs, payloadPreview: new_request.slice(0, 200) + '…' });

    try {
        const response = await fetch('/api/estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_request, case_history }),
            signal: controller.signal
        });

        console.log('[AI] /api/estimate response:', response.status, response.statusText);

        if (!response.ok) {
            let text = '';
            try { text = await response.text(); } catch {}
            const msg = `Server returned ${response.status} ${response.statusText}${text ? `\n\nBody:\n${text}` : ''}`;
            console.error('[AI] Non-OK response:', msg);
            alert(`AI Estimate failed.\n\n${msg}`);
            return;
        }

        const aiResponse = await response.json().catch(err => {
            console.error('[AI] JSON parse error:', err);
            throw new Error('Response was not valid JSON.');
        });

        console.log('[AI] Parsed JSON:', aiResponse);

        if (aiResponse && typeof aiResponse === 'object') {
            updateUIFromData(aiResponse);

            const laborTabBtn = document.querySelector('button[data-boetab="labor"]');
            if (laborTabBtn) laborTabBtn.click();
        } else {
            alert('AI returned an unexpected payload. Check console for details.');
            console.warn('[AI] Unexpected payload:', aiResponse);
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            console.error('[AI] Request timed out after', timeoutMs, 'ms');
            alert(`AI Estimate timed out after ${timeoutMs/1000}s. Check server availability or logs.`);
        } else {
            console.error('[AI] Estimation error:', error);
            alert(`Failed to get an estimate: ${error.message}`);
        }
    } finally {
        clearTimeout(timeoutId);
        btn.disabled = false;
        spinner.classList.add('hidden');
        btnText.textContent = "AI Estimate Full Project";
    }
}

async function handleGenerateBoe() {
    const btn = document.getElementById('generate-btn');
    const out = document.getElementById('output-section');
    const comp = document.getElementById('complete-state');
    const downloadExcelLink = document.getElementById('download-excel');
    const downloadPdfLink = document.getElementById('download-pdf');

    if (!btn || !out || !comp || !downloadExcelLink || !downloadPdfLink) {
        console.error("A required BoE element is missing from the page.");
        return;
    }

    btn.disabled = true;
    out.classList.remove('hidden');
    comp.classList.add('hidden');

    try {
        const projectData = getCurrentProjectData();
        const totals = calculateAllTotals(projectData);

        const excelResponse = await fetch('/api/generate-boe-excel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ projectData, totals })
        });

        if (!excelResponse.ok) {
            throw new Error('Failed to generate the Excel file on the server.');
        }
        const excelBlob = await excelResponse.blob();
        downloadExcelLink.href = URL.createObjectURL(excelBlob);
        downloadExcelLink.download = `BoE_${projectData.project_title.replace(/\s+/g, '_')}_Full.xlsx`;

        const pdfResponse = await fetch('/api/generate-boe-pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ projectData, totals })
        });
        
        if (!pdfResponse.ok) {
            throw new Error('Failed to generate the PDF file on the server.');
        }
        const pdfBlob = await pdfResponse.blob();
        downloadPdfLink.href = URL.createObjectURL(pdfBlob);
        downloadPdfLink.download = `BoE_${projectData.project_title.replace(/\s+/g, '_')}_Customer.pdf`;

        comp.classList.remove('hidden');

    } catch (error) {
        console.error("BoE Generation Error:", error);
        alert(`An error occurred while generating documents: ${error.message}`);
        out.classList.add('hidden');
    } finally {
        btn.disabled = false;
    }
}

function generatePdfFile(pTitle, totals) {
    try {
        const { jsPDF } = window.jspdf;
        const doc = new jsPDF();
        if (logoBase64) {
            doc.addImage(logoBase64, 'PNG', 14, 15, 50, 11);
        }
        doc.setFontSize(22); doc.setFont(undefined, 'bold'); doc.text("Basis of Estimate", 200, 22, { align: 'right' });
        doc.setLineWidth(0.5); doc.line(14, 30, 200, 30);
        doc.setFontSize(11); doc.setFont(undefined, 'bold'); doc.text("Project:", 14, 40);
        doc.setFont(undefined, 'normal'); doc.text(pTitle, 35, 40);
        doc.setFont(undefined, 'bold'); doc.text("Date:", 14, 46);
        doc.setFont(undefined, 'normal'); doc.text(new Date().toLocaleDateString('en-US'), 35, 46);
        const tableColumn = ["Cost Element", "Amount"];
        const tableRows = [
            ["Direct Labor", formatCurrency(totals.laborCost)], ["Materials & Tools", formatCurrency(totals.materialsCost)],
            ["Travel", formatCurrency(totals.travelCost)], ["Subcontracts", formatCurrency(totals.subcontractCost || 0)],
            [{ content: "Total Direct Costs", styles: { fontStyle: 'bold' } }, { content: formatCurrency(totals.totalDirectCosts), styles: { fontStyle: 'bold' } }],
            ["Indirect Costs (O/H + G&A)", formatCurrency(totals.overheadAmount + totals.gnaAmount)],
            [{ content: "Total Estimated Cost", styles: { fontStyle: 'bold' } }, { content: formatCurrency(totals.totalCost), styles: { fontStyle: 'bold' } }],
            ["Fee", formatCurrency(totals.feeAmount)],
            [{ content: "Total Proposed Price", styles: { fillColor: '#f7c42e', textColor: '#1a1a1a', fontStyle: 'bold' } }, { content: formatCurrency(totals.totalPrice), styles: { fillColor: '#f7c42e', textColor: '#1a1a1a', fontStyle: 'bold' } }]
        ];
        doc.autoTable({
            head: [tableColumn], body: tableRows, startY: 55, theme: 'grid',
            headStyles: { fillColor: '#333' }, styles: { fontSize: 11 },
            didDrawPage: function(data) {
                const pageCount = doc.internal.getNumberOfPages();
                doc.setFontSize(9); doc.setTextColor(150);
                doc.text("Generated by WBI BoE Tool", data.settings.margin.left, doc.internal.pageSize.height - 10);
                doc.text(`Page ${data.pageNumber} of ${pageCount}`, doc.internal.pageSize.width - data.settings.margin.right, doc.internal.pageSize.height - 10, { align: 'right' });
            }
        });
        document.getElementById('download-pdf').href = URL.createObjectURL(doc.output('blob'));
        document.getElementById('download-pdf').download = `BoE_${pTitle.replace(/\s+/g, '_')}_Customer.pdf`;
    } catch (e) {
        console.error("Error during PDF generation:", e);
        alert("An error occurred while generating the PDF.");
    }
}

function generateExcelFile(projectData, totals) {
    const wb = XLSX.utils.book_new();
    const summaryData = [
        ["Cost Element", "Amount"], ["Direct Labor", totals.laborCost], ["Materials & Tools", totals.materialsCost],
        ["Travel", totals.travelCost], ["Subcontracts", totals.subcontractCost || 0], ["Total Direct Costs", totals.totalDirectCosts],
        ["Indirect Costs (O/H + G&A)", totals.overheadAmount + totals.gnaAmount], ["Total Estimated Cost", totals.totalCost],
        ["Fee", totals.feeAmount], ["Total Proposed Price", totals.totalPrice]
    ];
    const summaryWs = XLSX.utils.aoa_to_sheet(summaryData);
    XLSX.utils.book_append_sheet(wb, summaryWs, "Cost Summary");

    const laborHeaders = ["Work Breakdown Structure Element", ...Object.keys(LABOR_RATES)];
    const laborData = [laborHeaders];
    projectData.work_plan.forEach(task => {
        const row = [task.task];
        Object.keys(LABOR_RATES).forEach(role => { row.push(task.hours[role] || 0); });
        laborData.push(row);
    });
    const laborWs = XLSX.utils.aoa_to_sheet(laborData);
    XLSX.utils.book_append_sheet(wb, laborWs, "Labor Detail");

    if (projectData.materials_and_tools && projectData.materials_and_tools.length > 0) {
        const materialsWs = XLSX.utils.json_to_sheet(projectData.materials_and_tools);
        XLSX.utils.book_append_sheet(wb, materialsWs, "Materials & Tools");
    }
    if (projectData.travel && projectData.travel.length > 0) {
        const travelWs = XLSX.utils.json_to_sheet(projectData.travel);
        XLSX.utils.book_append_sheet(wb, travelWs, "Travel Detail");
    }
    if (projectData.subcontracts && projectData.subcontracts.length > 0) {
        const subcontractsWs = XLSX.utils.json_to_sheet(projectData.subcontracts);
        XLSX.utils.book_append_sheet(wb, subcontractsWs, "Subcontracts");
    }

    const wbout = XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
    const blob = new Blob([wbout], { type: 'application/octet-stream' });
    
    document.getElementById('download-excel').href = URL.createObjectURL(blob);
    document.getElementById('download-excel').download = `BoE_${projectData.project_title.replace(/\s+/g, '_')}_Full.xlsx`;
}
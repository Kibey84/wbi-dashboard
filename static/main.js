document.addEventListener('DOMContentLoaded', () => {
let dotsInterval;
function startDots() {
    const dots = document.getElementById('dotPulse');
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

    // --- Tab 1: Opportunity Pipeline Logic ---
    const runPipelineBtn = document.getElementById('runPipelineBtn');
    if (runPipelineBtn) {
        runPipelineBtn.addEventListener('click', async () => {
            const progress = document.getElementById('pipelineProgress');
            const log = document.getElementById('logContainer');
            const oppsBtn = document.getElementById('downloadOppsBtn');
            const matchBtn = document.getElementById('downloadMatchBtn');
            progress.classList.remove('hidden');
            log.innerHTML = '<div class="text-gray-400">🚀 Starting...</div>';
            runPipelineBtn.disabled = true;
            oppsBtn.disabled = true;
            matchBtn.disabled = true;
            try {
                const response = await fetch('/api/run-pipeline', { method: 'POST' });
                const result = await response.json();
                log.innerHTML = '';
                result.log.forEach(msg => {
                    const logEntry = document.createElement('div');
                    logEntry.className = 'log-entry text-gray-300';
                    logEntry.innerHTML = msg.text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
                    log.appendChild(logEntry);
                });
                if(result.opps_report_filename) {
                    oppsBtn.onclick = () => window.location.href = `/download/${result.opps_report_filename}`;
                    oppsBtn.disabled = false;
                }
                if(result.match_report_filename) {
                    matchBtn.onclick = () => window.location.href = `/download/${result.match_report_filename}`;
                    matchBtn.disabled = false;
                }
            } catch (error) {
                log.innerHTML = `<div class="text-red-500">${error}</div>`;
            } finally {
                runPipelineBtn.disabled = false;
            }
        });
    }

    // --- Tab 2: Org Chart Parser Logic ---
    const fileUploadEl = document.getElementById('fileUpload');
    if(fileUploadEl) {
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
                const card = button.closest('.bg-white.rounded-xl');
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
        const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
        
        return `
            <div class="flex justify-between items-start">
                <h3 class="text-xl font-bold text-gray-800" data-project-name="${project.projectName}">${project.projectName}</h3>
                <span class="px-3 py-1 text-sm font-medium rounded-full ${project.status === 'Active' ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-800'}">${project.status}</span>
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
                    <div><label class="block text-sm font-medium text-gray-600">Month</label><select class="month-select mt-1 block w-full p-2 border border-gray-300 rounded-md">${months.map(m => `<option ${m === currentMonth ? 'selected' : ''}>${m}</option>`).join('')}</select></div>
                    <div><label class="block text-sm font-medium text-gray-600">Year</label><input type="number" class="year-input mt-1 block w-full p-2 border border-gray-300 rounded-md" value="${currentYear}"></div>
                </div>
                <div><label class="block text-sm font-medium text-gray-600">Your Update</label><textarea class="manager-update-textarea mt-1 block w-full p-2 border border-gray-300 rounded-md" rows="4" placeholder="Enter your monthly progress update here..."></textarea></div>
                <div class="mt-4"><label class="block text-sm font-medium text-gray-600">AI-Generated Quarterly Summary</label><div class="ai-summary-box mt-1 block w-full p-2 bg-white border border-gray-300 rounded-md min-h-[100px]"></div></div>
                <button class="generate-save-btn mt-4 w-full md:w-auto float-right bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg transition duration-200" data-project-name="${project.projectName}" data-description="${project.description}">Generate & Save</button>
            </div>
        `;
    }
    
    // Initial population of the PM filter when the page first loads
    populatePmFilter();
});
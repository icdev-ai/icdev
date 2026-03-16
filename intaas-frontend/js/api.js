// INTaaS API Client
const API_BASE = 'https://spf6ebulf436uhxp7cvy6rrva40dvlad.lambda-url.us-east-1.on.aws';
const API_KEY = localStorage.getItem('intaas_api_key') || '';

const api = {
  async request(method, path, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    // Only send API key for write operations (POST/PUT/DELETE)
    const key = localStorage.getItem('intaas_api_key') || '';
    if (key && method !== 'GET') headers['Authorization'] = `Bearer ${key}`;
    // Also send for GET if available (some endpoints may return more data)
    if (key && method === 'GET') headers['Authorization'] = `Bearer ${key}`;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);

    const resp = await fetch(`${API_BASE}${path}`, opts);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    return resp.json();
  },

  // Analysis
  analyze(data) { return this.request('POST', '/api/v1/analyze', data); },
  listTopics() { return this.request('GET', '/api/v1/topics'); },
  getTopic(id) { return this.request('GET', `/api/v1/topics/${id}`); },

  // Perspectives
  getPerspectives(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}`); },
  getCoverageGaps(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/gaps`); },
  getContrarian(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/contrarian`); },

  // Bias
  getBias(articleId) { return this.request('GET', `/api/v1/bias/${articleId}`); },
  deepAnalysis(articleId) { return this.request('POST', `/api/v1/bias/${articleId}/deep-analysis`); },

  // Socratic
  socratic(topicId, data) { return this.request('POST', `/api/v1/socratic/${topicId}`, data); },
  getSession(sessionId) { return this.request('GET', `/api/v1/socratic/${sessionId}`); },

  // Judge
  judge(data) { return this.request('POST', '/api/v1/judge/evaluate', data); },
  getJudge(articleId) { return this.request('GET', `/api/v1/judge/${articleId}`); },

  // Sources
  getSources() { return this.request('GET', '/api/v1/sources'); },
  registerSource(data) { return this.request('POST', '/api/v1/sources/register', data); },
  uploadArticle(data) { return this.request('POST', '/api/v1/sources/upload', data); },

  // Reports
  generateReport(topicId, template) { return this.request('GET', `/api/v1/perspectives/${topicId}/report/${template}`); },

  // Entity Analysis
  getEntities(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/entities`); },
  getGraphAnalysis(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/graph-analysis`); },
  getRelatedTopics(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/related`); },

  // Narrative Tracking
  getNarrative(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/narrative`); },
  takeSnapshot(topicId) { return this.request('POST', `/api/v1/perspectives/${topicId}/narrative/snapshot`); },
  getShifts(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/narrative/shifts`); },

  // Claims
  getClaims(topicId) { return this.request('GET', `/api/v1/perspectives/${topicId}/claims`); },

  // Source Report Cards
  getReportCards() { return this.request('GET', '/api/v1/sources/report-cards'); },
  getReportCard(domain) { return this.request('GET', `/api/v1/sources/report-card/${domain}`); },

  // Search
  search(query) { return this.request('GET', `/api/v1/search?q=${encodeURIComponent(query)}`); },

  // Health
  health() { return this.request('GET', '/health'); },
};

function setApiKey(key) {
  localStorage.setItem('intaas_api_key', key);
  location.reload();
}

// Bias color helpers
function biasColorClass(color) {
  return `bias-${color || 'green'}`;
}

function biasBarColor(score) {
  if (score >= 4.5) return 'var(--bias-blue)';
  if (score >= 3.8) return 'var(--bias-purple)';
  if (score >= 3.0) return 'var(--bias-green)';
  if (score >= 2.0) return 'var(--bias-yellow)';
  return 'var(--bias-red)';
}

function statusClass(status) {
  return `status-${status || 'analyzing'}`;
}

function timeAgo(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

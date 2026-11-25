import React, { useState, useEffect, useRef, useMemo } from 'react';

const DEFAULT_API_KEY = 'ocr-dev-api-key-change-in-production';

const HARDCODED_USERS = [
  { username: 'admin', password: 'password123', role: 'Administrator' },
  { username: 'admin', password: 'admin123', role: 'Administrator' },
  { username: 'utec', password: 'utec123', role: 'Operator' },
];

const TEMPLATE_META = {
  safwa: {
    label: 'Safwa Hospital',
    arabic: 'مستشفى الصفوة الدولي',
    badge: 'Inpatient & Outpatient',
  },
  massara: {
    label: 'Massara Clinic',
    arabic: 'مصحة المسرة',
    badge: 'Invoices & ID Cards',
  },
  musafat: {
    label: 'Muasifat Clinic',
    arabic: 'مصحة المواصفات',
    badge: 'Receipts & Claims',
  },
  janzour: {
    label: 'Janzour Clinic',
    arabic: 'عيادة جنزور',
    badge: 'Full Claim Packet',
  },
};

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i === 0 ? 0 : 1)} ${sizes[i]}`;
}

/**
 * Syntax-highlighted JSON renderer that handles arbitrarily nested objects/arrays
 * and optional search term highlighting.
 */
function SyntaxHighlightedJson({ data, searchTerm = '' }) {
  const jsonString = useMemo(() => {
    try {
      return JSON.stringify(data, null, 2) || '{}';
    } catch {
      return String(data);
    }
  }, [data]);

  const highlightedLines = useMemo(() => {
    const lines = jsonString.split('\n');
    const tokenRegex =
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g;

    return lines.map((line, idx) => {
      if (
        searchTerm &&
        !line.toLowerCase().includes(searchTerm.trim().toLowerCase())
      ) {
        // Still show all lines, or highlight matching lines
      }
      const parts = [];
      let lastIndex = 0;
      let match;
      while ((match = tokenRegex.exec(line)) !== null) {
        if (match.index > lastIndex) {
          parts.push(
            <span key={`p-${lastIndex}`} className="json-punctuation">
              {line.slice(lastIndex, match.index)}
            </span>
          );
        }
        const token = match[0];
        let cls = 'json-number';
        if (/^"/.test(token)) {
          if (/:$/.test(token)) {
            cls = 'json-key';
          } else {
            cls = 'json-string';
          }
        } else if (/true|false/.test(token)) {
          cls = 'json-boolean';
        } else if (/null/.test(token)) {
          cls = 'json-null';
        }
        parts.push(
          <span key={`t-${match.index}`} className={cls}>
            {token}
          </span>
        );
        lastIndex = match.index + token.length;
      }
      if (lastIndex < line.length) {
        parts.push(
          <span key={`e-${lastIndex}`} className="json-punctuation">
            {line.slice(lastIndex)}
          </span>
        );
      }
      const isMatch =
        searchTerm.trim() &&
        line.toLowerCase().includes(searchTerm.trim().toLowerCase());

      return (
        <div
          key={idx}
          style={{
            display: 'flex',
            backgroundColor: isMatch ? 'rgba(56, 189, 248, 0.18)' : 'transparent',
            borderRadius: 4,
            padding: '0 4px',
          }}
        >
          <span
            style={{
              width: 38,
              flexShrink: 0,
              color: '#475569',
              userSelect: 'none',
              textAlign: 'right',
              paddingRight: 12,
              fontSize: '0.75rem',
            }}
          >
            {idx + 1}
          </span>
          <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {parts}
          </span>
        </div>
      );
    });
  }, [jsonString, searchTerm]);

  return <div>{highlightedLines}</div>;
}

/**
 * Visual summary component for an extracted document result alongside the JSON.
 */
function StructuredPreview({ result }) {
  if (!result) {
    return (
      <div className="structured-grid">
        <div style={{ color: 'var(--text-secondary)', fontSize: '0.88rem' }}>
          Waiting for extraction result to complete...
        </div>
      </div>
    );
  }

  const invoices = Array.isArray(result.invoices) ? result.invoices : [];
  const idCards = Array.isArray(result.id_cards) ? result.id_cards : [];
  const otherPages = Array.isArray(result.other_pages) ? result.other_pages : [];
  const errors = Array.isArray(result.errors) ? result.errors : [];

  return (
    <div className="structured-grid">
      <div className="kv-grid">
        <div className="kv-box">
          <div className="kv-label">Filename</div>
          <div className="kv-val">{result.filename || '—'}</div>
        </div>
        <div className="kv-box">
          <div className="kv-label">Template</div>
          <div className="kv-val" style={{ textTransform: 'capitalize' }}>
            {result.template || '—'}
          </div>
        </div>
        <div className="kv-box">
          <div className="kv-label">Invoices Merged</div>
          <div className="kv-val">{invoices.length}</div>
        </div>
        <div className="kv-box">
          <div className="kv-label">ID Cards Extracted</div>
          <div className="kv-val">{idCards.length}</div>
        </div>
      </div>

      {idCards.length > 0 && (
        <div className="invoice-card">
          <div style={{ fontWeight: 700, fontSize: '0.92rem', color: '#38bdf8' }}>
            Extracted ID Cards ({idCards.length})
          </div>
          <div className="kv-grid">
            {idCards.map((card, i) => {
              const d = card.data || {};
              return (
                <div key={i} className="kv-box">
                  <div className="kv-label">
                    Page {card.page} ({card.source || 'detector'})
                  </div>
                  <div className="kv-val">{d.card_number || 'No Card #'}</div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginTop: 4 }}>
                    {d.beneficiary_name || d.employee_name || ''}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {invoices.map((inv, idx) => {
        const patient = inv.patient || {};
        const sections = Array.isArray(inv.sections) ? inv.sections : [];
        return (
          <div key={idx} className="invoice-card">
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 8,
              }}
            >
              <div style={{ fontWeight: 700, fontSize: '0.95rem', color: '#34d399' }}>
                Invoice #{idx + 1}{' '}
                {inv.document_title ? `— ${inv.document_title}` : ''}{' '}
                {inv.hospital_name ? `(${inv.hospital_name})` : ''}
              </div>
              <span className="stat-chip">
                Pages: {Array.isArray(inv.pages) ? inv.pages.join(', ') : '—'}
              </span>
            </div>

            <div className="kv-grid">
              {Object.entries(patient).map(([k, v]) =>
                v ? (
                  <div key={k} className="kv-box">
                    <div className="kv-label">{k.replace(/_/g, ' ')}</div>
                    <div className="kv-val">{String(v)}</div>
                  </div>
                ) : null
              )}
              {inv.invoice_number && (
                <div className="kv-box">
                  <div className="kv-label">Invoice Number</div>
                  <div className="kv-val">{inv.invoice_number}</div>
                </div>
              )}
              {inv.receipt?.invoice_number && (
                <div className="kv-box">
                  <div className="kv-label">Receipt / Invoice #</div>
                  <div className="kv-val">{inv.receipt.invoice_number}</div>
                </div>
              )}
            </div>

            {sections.map((sec, sIdx) => (
              <div key={sIdx} style={{ overflowX: 'auto' }}>
                <div
                  style={{
                    fontSize: '0.82rem',
                    fontWeight: 700,
                    color: '#7dd3fc',
                    marginBottom: 6,
                  }}
                >
                  Section: {sec.section_name || 'Untitled'} (
                  {(sec.items || []).length} items)
                </div>
                {Array.isArray(sec.items) && sec.items.length > 0 && (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Code</th>
                        <th>Description</th>
                        <th>Date</th>
                        <th>Qty</th>
                        <th>Unit Price</th>
                        <th>Net Price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sec.items.map((item, iIdx) => (
                        <tr key={iIdx}>
                          <td>{item.code ?? '—'}</td>
                          <td>
                            {item.description || item.english_description || '—'}
                          </td>
                          <td>{item.date ?? '—'}</td>
                          <td>{item.quantity ?? '—'}</td>
                          <td>{item.unit_price ?? '—'}</td>
                          <td>{item.net_price ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))}
          </div>
        );
      })}

      {otherPages.length > 0 && (
        <div className="invoice-card">
          <div style={{ fontWeight: 700, fontSize: '0.88rem' }}>
            Other Classified Pages ({otherPages.length})
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {otherPages.map((p, i) => (
              <span key={i} className="stat-chip">
                Page {p.page}: {p.document_type}
              </span>
            ))}
          </div>
        </div>
      )}

      {errors.length > 0 && (
        <div className="error-banner">
          <div>
            <strong>Page Extraction Warnings ({errors.length}):</strong>{' '}
            {JSON.stringify(errors)}
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  // Login / Session state (hardcoded credentials)
  const [authUser, setAuthUser] = useState(() => {
    try {
      const saved = sessionStorage.getItem('ocr_auth_user');
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loginError, setLoginError] = useState('');

  // Backend & auth state
  const [apiKey, setApiKey] = useState(
    () => localStorage.getItem('ocr_api_key') || DEFAULT_API_KEY
  );
  const [apiBaseUrl, setApiBaseUrl] = useState(
    () => localStorage.getItem('ocr_api_base') || ''
  );
  const [showSettings, setShowSettings] = useState(false);
  const [health, setHealth] = useState({
    status: 'checking',
    redis_ok: false,
    templates_loaded: 0,
  });

  // Templates & Upload Form state
  const [pipelineMode, setPipelineMode] = useState('ocr'); // 'ocr' | 'epdf'
  const [ocrTemplates, setOcrTemplates] = useState([
    'safwa',
    'massara',
    'musafat',
    'janzour',
  ]);
  const [epdfTemplates, setEpdfTemplates] = useState(['janzour']);
  const [selectedTemplate, setSelectedTemplate] = useState('safwa');
  const [files, setFiles] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [includeRawPages, setIncludeRawPages] = useState(false);

  const activeTemplates = pipelineMode === 'epdf' ? epdfTemplates : ocrTemplates;

  // Submission & Active Jobs state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [batchId, setBatchId] = useState(null);
  const [jobs, setJobs] = useState([]); // Array of JobStatusResponse objects
  const [activeTab, setActiveTab] = useState('all'); // 'all' or task_id
  const [viewMode, setViewMode] = useState('json'); // 'json' | 'full_job' | 'visual'
  const [searchTerm, setSearchTerm] = useState('');
  const [copiedId, setCopiedId] = useState(null);
  const [lookupId, setLookupId] = useState('');

  const fileInputRef = useRef(null);
  const wsRef = useRef(null);

  const buildUrl = (path) => {
    const base = apiBaseUrl.trim().replace(/\/+$/, '');
    return `${base}${path}`;
  };

  const authHeaders = useMemo(() => {
    const headers = {};
    if (apiKey.trim()) {
      headers['X-API-Key'] = apiKey.trim();
    }
    return headers;
  }, [apiKey]);

  // Save settings to localStorage
  useEffect(() => {
    localStorage.setItem('ocr_api_key', apiKey);
  }, [apiKey]);

  useEffect(() => {
    localStorage.setItem('ocr_api_base', apiBaseUrl);
  }, [apiBaseUrl]);

  // Check API health & load templates list
  useEffect(() => {
    let cancelled = false;

    async function fetchHealthAndTemplates() {
      try {
        const res = await fetch(buildUrl('/api/health/'));
        if (res.ok) {
          const data = await res.json();
          if (!cancelled) setHealth(data);
        } else {
          if (!cancelled) setHealth({ status: 'offline', redis_ok: false, templates_loaded: 0 });
        }
      } catch {
        if (!cancelled) setHealth({ status: 'offline', redis_ok: false, templates_loaded: 0 });
      }

      try {
        const tRes = await fetch(buildUrl('/api/health/templates'), {
          headers: authHeaders,
        });
        if (tRes.ok) {
          const tData = await tRes.json();
          if (!cancelled && tData.ok && Array.isArray(tData.templates) && tData.templates.length > 0) {
            setOcrTemplates(tData.templates);
          }
        }
      } catch {
        // Keep default OCR templates list
      }

      try {
        const eRes = await fetch(buildUrl('/api/epdf/templates'), {
          headers: authHeaders,
        });
        if (eRes.ok) {
          const eData = await eRes.json();
          if (!cancelled && eData.ok && Array.isArray(eData.templates) && eData.templates.length > 0) {
            setEpdfTemplates(eData.templates);
          }
        }
      } catch {
        // Keep default e-PDF templates list
      }
    }

    fetchHealthAndTemplates();
    const interval = setInterval(fetchHealthAndTemplates, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [apiBaseUrl, authHeaders]);

  // Keep selectedTemplate valid whenever mode or fetched templates change
  useEffect(() => {
    if (activeTemplates.length > 0 && !activeTemplates.includes(selectedTemplate)) {
      setSelectedTemplate(activeTemplates[0]);
    }
  }, [pipelineMode, activeTemplates, selectedTemplate]);

  // Poll active jobs/batch until all jobs are completed or error
  useEffect(() => {
    const hasActiveJobs = jobs.some(
      (j) => j.status !== 'completed' && j.status !== 'error'
    );
    if (!hasActiveJobs && jobs.length === 0) return;
    if (!hasActiveJobs) return;

    let cancelled = false;

    async function pollStatus() {
      try {
        if (batchId) {
          const res = await fetch(
            buildUrl(
              `/api/batches/${encodeURIComponent(
                batchId
              )}?include_results=true&include_raw_pages=${includeRawPages}`
            ),
            { headers: authHeaders }
          );
          if (res.ok) {
            const data = await res.json();
            if (!cancelled && Array.isArray(data.jobs) && data.jobs.length > 0) {
              setJobs(data.jobs);
              return;
            }
          }
        }

        // Poll individual jobs
        const updated = await Promise.all(
          jobs.map(async (job) => {
            if (job.status === 'completed' && job.result) return job;
            try {
              const res = await fetch(
                buildUrl(
                  `/api/jobs/${encodeURIComponent(
                    job.task_id
                  )}?include_raw_pages=${includeRawPages}`
                ),
                { headers: authHeaders }
              );
              if (res.ok) {
                return await res.json();
              }
            } catch {
              // ignore transient error
            }
            return job;
          })
        );
        if (!cancelled) {
          setJobs(updated);
        }
      } catch {
        // ignore poll error
      }
    }

    pollStatus();
    const timer = setInterval(pollStatus, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [jobs, batchId, includeRawPages, authHeaders, apiBaseUrl]);

  // Re-fetch completed jobs if user toggles includeRawPages
  useEffect(() => {
    if (jobs.length === 0) return;
    let cancelled = false;
    async function refreshJobs() {
      const updated = await Promise.all(
        jobs.map(async (job) => {
          try {
            const res = await fetch(
              buildUrl(
                `/api/jobs/${encodeURIComponent(
                  job.task_id
                )}?include_raw_pages=${includeRawPages}`
              ),
              { headers: authHeaders }
            );
            if (res.ok) return await res.json();
          } catch {
            // ignore
          }
          return job;
        })
      );
      if (!cancelled) setJobs(updated);
    }
    refreshJobs();
    return () => {
      cancelled = true;
    };
  }, [includeRawPages]);

  // Connect WebSocket for instant real-time updates
  useEffect(() => {
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {}
      wsRef.current = null;
    }

    const hasActive = jobs.some(
      (j) => j.status !== 'completed' && j.status !== 'error'
    );
    if (!hasActive) return;

    const firstTaskId = jobs[0]?.task_id;
    if (!batchId && !firstTaskId) return;

    const base = apiBaseUrl.trim().replace(/\/+$/, '');
    let wsOrigin;
    if (base.startsWith('http://') || base.startsWith('https://')) {
      wsOrigin = base.replace(/^http/, 'ws');
    } else {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsOrigin = `${proto}//${window.location.host}`;
    }

    const scopePath = batchId
      ? `/ws/batch/${encodeURIComponent(batchId)}`
      : `/ws/${encodeURIComponent(firstTaskId)}`;
    const wsUrl = `${wsOrigin}${scopePath}?api_key=${encodeURIComponent(
      apiKey.trim()
    )}`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (!msg || !msg.task_id) return;
          setJobs((prevJobs) =>
            prevJobs.map((j) => {
              if (j.task_id !== msg.task_id) return j;
              if (msg.event === 'completed' && msg.data) {
                return {
                  ...j,
                  status: 'completed',
                  completed_pages: j.page_count || j.completed_pages,
                  pending_pages: 0,
                  result: msg.data,
                };
              }
              if (msg.event === 'error') {
                return {
                  ...j,
                  status: 'error',
                  error: msg.data?.message || 'Extraction error',
                };
              }
              if (msg.event === 'step_completed' && msg.data?.page) {
                return {
                  ...j,
                  status: 'processing',
                  completed_pages: Math.max(
                    j.completed_pages || 0,
                    Number(msg.data.page) || 0
                  ),
                };
              }
              if (msg.event === 'progress' && msg.data?.pages) {
                return {
                  ...j,
                  status: 'processing',
                  page_count: Number(msg.data.pages) || j.page_count,
                };
              }
              return j;
            })
          );
        } catch {
          // ignore malformed WS event
        }
      };
    } catch {
      // Polling fallback handles everything if WebSocket fails
    }

    return () => {
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {}
        wsRef.current = null;
      }
    };
  }, [batchId, jobs.length, apiKey, apiBaseUrl]);

  // Add selected files
  const handleAddFiles = (incomingList) => {
    setErrorMsg('');
    const incoming = Array.from(incomingList || []);
    const pdfs = incoming.filter((f) => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfs.length < incoming.length) {
      setErrorMsg('Only PDF files (.pdf) are accepted.');
    }
    setFiles((prev) => {
      const existingMap = new Map(prev.map((f) => [`${f.name}-${f.size}`, f]));
      for (const f of pdfs) {
        existingMap.set(`${f.name}-${f.size}`, f);
      }
      return Array.from(existingMap.values());
    });
  };

  const handleRemoveFile = (index) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  // Submit one or multiple PDFs
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (files.length === 0) {
      setErrorMsg('Please select at least one PDF file to process.');
      return;
    }

    setErrorMsg('');
    setIsSubmitting(true);

    try {
      const isEpdf = pipelineMode === 'epdf';
      const singleEndpoint = isEpdf ? '/api/epdf/process' : '/process-pdf/';
      const batchEndpoint = isEpdf ? '/api/epdf/batch' : '/batch-process-pdf/';

      if (files.length === 1) {
        const formData = new FormData();
        formData.append('template', selectedTemplate);
        formData.append('file', files[0]);

        const res = await fetch(buildUrl(singleEndpoint), {
          method: 'POST',
          headers: authHeaders,
          body: formData,
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(
            errData.detail || `Upload failed with status ${res.status}`
          );
        }

        const data = await res.json();
        setBatchId(null);
        const initialJob = {
          task_id: data.task_id,
          filename: data.filename || files[0].name,
          template: data.template || selectedTemplate,
          pipeline: data.pipeline || pipelineMode,
          status: data.status || 'queued',
          page_count: data.page_count || 0,
          completed_pages: 0,
          pending_pages: data.page_count || 0,
          result: null,
        };
        setJobs([initialJob]);
        setActiveTab(data.task_id);
      } else {
        const formData = new FormData();
        formData.append('template', selectedTemplate);
        for (const file of files) {
          formData.append('files', file);
        }

        const res = await fetch(buildUrl(batchEndpoint), {
          method: 'POST',
          headers: authHeaders,
          body: formData,
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(
            errData.detail || `Batch upload failed with status ${res.status}`
          );
        }

        const list = await res.json();
        if (!Array.isArray(list) || list.length === 0) {
          throw new Error('Server returned no queued jobs for this batch.');
        }

        const newBatchId = list[0]?.batch_id || null;
        setBatchId(newBatchId);
        const initialJobs = list.map((item) => ({
          task_id: item.task_id,
          filename: item.filename,
          batch_id: item.batch_id,
          template: item.template || selectedTemplate,
          pipeline: item.pipeline || pipelineMode,
          status: item.status || 'queued',
          page_count: item.page_count || 0,
          completed_pages: 0,
          pending_pages: item.page_count || 0,
          result: null,
        }));
        setJobs(initialJobs);
        setActiveTab('all');
      }
    } catch (err) {
      setErrorMsg(err.message || 'Failed to submit PDF(s).');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Lookup an existing Task ID or Batch ID
  const handleLookup = async (e) => {
    e.preventDefault();
    const id = lookupId.trim();
    if (!id) return;
    setErrorMsg('');
    setIsSubmitting(true);
    try {
      // Try job first
      const jobRes = await fetch(
        buildUrl(
          `/api/jobs/${encodeURIComponent(id)}?include_raw_pages=${includeRawPages}`
        ),
        { headers: authHeaders }
      );
      if (jobRes.ok) {
        const jobData = await jobRes.json();
        setBatchId(jobData.batch_id || null);
        setJobs([jobData]);
        setActiveTab(jobData.task_id);
        setIsSubmitting(false);
        return;
      }

      // Try batch next
      const batchRes = await fetch(
        buildUrl(
          `/api/batches/${encodeURIComponent(
            id
          )}?include_results=true&include_raw_pages=${includeRawPages}`
        ),
        { headers: authHeaders }
      );
      if (batchRes.ok) {
        const batchData = await batchRes.json();
        setBatchId(batchData.batch_id);
        setJobs(batchData.jobs || []);
        setActiveTab('all');
        setIsSubmitting(false);
        return;
      }

      throw new Error(`No Job or Batch found with ID "${id}"`);
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Compute displayed JSON payload based on activeTab and viewMode
  const displayedJson = useMemo(() => {
    if (jobs.length === 0) return null;

    const formatJobOutput = (job) => {
      if (viewMode === 'full_job') {
        return job;
      }
      // Default 'json' view shows the extracted result JSON once completed,
      // or the live job status JSON while queued/processing.
      if (job.result) {
        if (!includeRawPages && job.result.raw_pages) {
          const { raw_pages, ...cleanResult } = job.result;
          return cleanResult;
        }
        return job.result;
      }
      return job;
    };

    if (activeTab === 'all' || jobs.length === 1) {
      if (jobs.length === 1) {
        return formatJobOutput(jobs[0]);
      }
      return jobs.map(formatJobOutput);
    }

    const selected = jobs.find((j) => j.task_id === activeTab) || jobs[0];
    return formatJobOutput(selected);
  }, [jobs, activeTab, viewMode, includeRawPages]);

  const selectedJobObj = useMemo(() => {
    if (jobs.length === 0) return null;
    if (activeTab === 'all') return jobs[0];
    return jobs.find((j) => j.task_id === activeTab) || jobs[0];
  }, [jobs, activeTab]);

  // Overall progress metrics
  const progressStats = useMemo(() => {
    const totalJobs = jobs.length;
    const completedJobs = jobs.filter((j) => j.status === 'completed').length;
    const errorJobs = jobs.filter((j) => j.status === 'error').length;
    const totalPages = jobs.reduce((acc, j) => acc + (j.page_count || 0), 0);
    const donePages = jobs.reduce(
      (acc, j) =>
        acc +
        (j.status === 'completed'
          ? j.page_count || j.completed_pages || 0
          : j.completed_pages || 0),
      0
    );
    const percent =
      totalPages > 0
        ? Math.min(100, Math.round((donePages / totalPages) * 100))
        : totalJobs > 0
        ? Math.round(((completedJobs + errorJobs) / totalJobs) * 100)
        : 0;

    return {
      totalJobs,
      completedJobs,
      errorJobs,
      totalPages,
      donePages,
      percent,
    };
  }, [jobs]);

  const handleCopyJson = () => {
    if (!displayedJson) return;
    navigator.clipboard.writeText(JSON.stringify(displayedJson, null, 2));
    setCopiedId('main');
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleDownloadJson = () => {
    if (!displayedJson) return;
    const blob = new Blob([JSON.stringify(displayedJson, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const filename =
      activeTab === 'all' && jobs.length > 1
        ? `batch_${batchId || 'results'}.json`
        : `${(selectedJobObj?.filename || 'result').replace(/\.pdf$/i, '')}.json`;
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleLoginSubmit = (e) => {
    e.preventDefault();
    setLoginError('');
    const matched = HARDCODED_USERS.find(
      (u) =>
        u.username === loginUsername.trim().toLowerCase() &&
        u.password === loginPassword
    );
    if (!matched) {
      setLoginError(
        'Invalid username or password. Use admin / admin123 (or click Fill Demo).'
      );
      return;
    }
    const userPayload = { username: matched.username, role: matched.role };
    setAuthUser(userPayload);
    try {
      sessionStorage.setItem('ocr_auth_user', JSON.stringify(userPayload));
    } catch {}
  };

  const handleLogout = () => {
    setAuthUser(null);
    setLoginUsername('');
    setLoginPassword('');
    try {
      sessionStorage.removeItem('ocr_auth_user');
    } catch {}
  };

  if (!authUser) {
    return (
      <div className="login-wrapper">
        <form className="login-card" onSubmit={handleLoginSubmit}>
          <div className="login-header">
            <div className="login-logo">
              <svg
                width="28"
                height="28"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
            </div>
            <div>
              <div className="login-title">MedOCR Studio</div>
              <div className="login-subtitle">
                Sign in to access the Medical PDF Batch Extraction Portal
              </div>
            </div>
          </div>

          <div className="field-group">
            <label className="field-label" htmlFor="login-username">
              <span>Username</span>
            </label>
            <input
              id="login-username"
              type="text"
              className="text-input"
              placeholder="Enter username (e.g. admin)"
              value={loginUsername}
              onChange={(e) => setLoginUsername(e.target.value)}
              autoFocus
              required
            />
          </div>

          <div className="field-group">
            <label className="field-label" htmlFor="login-password">
              <span>Password</span>
            </label>
            <div className="password-field-wrap">
              <input
                id="login-password"
                type={showPassword ? 'text' : 'password'}
                className="text-input"
                style={{ paddingRight: 64 }}
                placeholder="Enter password"
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                required
              />
              <button
                type="button"
                className="password-toggle-btn"
                onClick={() => setShowPassword((p) => !p)}
              >
                {showPassword ? 'HIDE' : 'SHOW'}
              </button>
            </div>
          </div>

          {loginError && (
            <div className="error-banner">
              <span>⚠️</span>
              <span>{loginError}</span>
            </div>
          )}

          <button type="submit" className="btn-submit">
            Sign In to Studio
          </button>

          <div className="credentials-hint-box">
            <div>
              <div style={{ color: 'var(--text-secondary)', marginBottom: 2 }}>
                Hardcoded Credentials:
              </div>
              <div className="credentials-hint-code">
                admin / admin123
              </div>
            </div>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setLoginUsername('admin');
                setLoginPassword('admin123');
                setLoginError('');
              }}
            >
              Fill Demo
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="app-shell">
      {/* Header */}
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <line x1="10" y1="9" x2="8" y2="9" />
            </svg>
          </div>
          <div>
            <div className="brand-title">
              MedOCR Studio
              <span className="version-pill">v3.0</span>
            </div>
            <div className="brand-subtitle">
              Medical Claim &amp; Invoice PDF Batch Extraction Pipeline
            </div>
          </div>
        </div>

        <div className="topbar-right">
          <div className="health-badge" title="Backend & Redis Live Status">
            <span
              className={`status-dot ${
                health.status === 'healthy'
                  ? 'healthy'
                  : health.status === 'degraded'
                  ? 'degraded'
                  : 'offline'
              }`}
            />
            <span>
              API: {health.status === 'healthy' ? 'Online' : health.status}
            </span>
            <span style={{ color: 'var(--text-muted)' }}>|</span>
            <span>{ocrTemplates.length} OCR</span>
            <span style={{ color: 'var(--text-muted)' }}>·</span>
            <span>{epdfTemplates.length} e-PDF</span>
          </div>

          <button
            type="button"
            className={`btn-ghost ${showSettings ? 'active' : ''}`}
            onClick={() => setShowSettings((s) => !s)}
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            API &amp; Job Lookup
          </button>

          <div className="user-chip">
            <span>👤 {authUser.username}</span>
          </div>

          <button
            type="button"
            className="btn-ghost"
            onClick={handleLogout}
            title="Sign out"
          >
            Sign Out
          </button>
        </div>
      </header>

      {/* Collapsible API Settings & Job Lookup Drawer */}
      {showSettings && (
        <section className="settings-drawer">
          <div className="field-group">
            <label className="field-label">
              <span>X-API-Key Authentication</span>
              <span style={{ color: 'var(--accent-emerald)', fontSize: '0.7rem' }}>
                Active
              </span>
            </label>
            <input
              type="text"
              className="text-input mono"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter X-API-Key..."
            />
          </div>

          <div className="field-group">
            <label className="field-label">
              <span>Backend Base URL (Leave blank for built-in proxy)</span>
            </label>
            <input
              type="text"
              className="text-input mono"
              value={apiBaseUrl}
              onChange={(e) => setApiBaseUrl(e.target.value)}
              placeholder="Same-origin proxy (/api, /process-pdf/)"
            />
          </div>

          <form className="field-group" onSubmit={handleLookup}>
            <label className="field-label">
              <span>Inspect Existing Task ID or Batch ID</span>
            </label>
            <div className="input-row">
              <input
                type="text"
                className="text-input mono"
                value={lookupId}
                onChange={(e) => setLookupId(e.target.value)}
                placeholder="Paste task_id or batch_id..."
              />
              <button type="submit" className="btn-ghost">
                Load JSON
              </button>
            </div>
          </form>
        </section>
      )}

      {/* Main Two-Column Grid */}
      <main className="workspace-grid">
        {/* Left Column: Upload & Template Form */}
        <form className="panel-card" onSubmit={handleSubmit}>
          <div className="panel-header">
            <div className="panel-title">
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#38bdf8"
                strokeWidth="2.2"
              >
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              Upload PDFs &amp; Select Template
            </div>
            {files.length > 0 && (
              <span className="version-pill">
                {files.length} {files.length === 1 ? 'PDF' : 'PDFs'} staged
              </span>
            )}
          </div>

          {/* Pipeline Mode Switcher: Scanned OCR vs Electronic PDF */}
          <div className="field-group">
            <label className="field-label">
              <span>Extraction Pipeline</span>
              <span
                style={{
                  textTransform: 'none',
                  color:
                    pipelineMode === 'epdf'
                      ? 'var(--accent-emerald)'
                      : 'var(--accent-primary)',
                }}
              >
                {pipelineMode === 'epdf' ? 'Text-Layer e-PDF' : 'Vision / Image OCR'}
              </span>
            </label>
            <div className="pipeline-switcher">
              <button
                type="button"
                className={`pipeline-option ${
                  pipelineMode === 'ocr' ? 'active ocr' : ''
                }`}
                onClick={() => {
                  setPipelineMode('ocr');
                  setErrorMsg('');
                }}
              >
                <span className="pipeline-option-title">Scanned / Image OCR</span>
                <span className="pipeline-option-sub">/process-pdf/</span>
              </button>
              <button
                type="button"
                className={`pipeline-option ${
                  pipelineMode === 'epdf' ? 'active epdf' : ''
                }`}
                onClick={() => {
                  setPipelineMode('epdf');
                  setErrorMsg('');
                }}
              >
                <span className="pipeline-option-title">Electronic PDF (e-PDF)</span>
                <span className="pipeline-option-sub">/api/epdf/process</span>
              </button>
            </div>
          </div>

          {/* Template Dropdown */}
          <div className="field-group">
            <label className="field-label" htmlFor="template-select">
              <span>
                {pipelineMode === 'epdf'
                  ? 'e-PDF Provider Template'
                  : 'Extraction Template'}
              </span>
              <span
                style={{
                  textTransform: 'none',
                  color:
                    pipelineMode === 'epdf'
                      ? 'var(--accent-emerald)'
                      : 'var(--accent-primary)',
                }}
              >
                {pipelineMode === 'epdf'
                  ? `epdf/${selectedTemplate}`
                  : `app/templates/${selectedTemplate}.json`}
              </span>
            </label>
            <div className="template-select-wrapper">
              <select
                id="template-select"
                className="select-input"
                value={selectedTemplate}
                onChange={(e) => setSelectedTemplate(e.target.value)}
              >
                {activeTemplates.map((tpl) => {
                  const meta = TEMPLATE_META[tpl];
                  return (
                    <option key={tpl} value={tpl}>
                      {tpl.toUpperCase()} —{' '}
                      {meta ? `${meta.label} (${meta.arabic})` : tpl}
                    </option>
                  );
                })}
              </select>
            </div>

            {/* Quick-pick visual template cards */}
            <div className="template-pills">
              {activeTemplates.map((tpl) => {
                const meta = TEMPLATE_META[tpl] || {
                  label: tpl,
                  arabic: 'Template Schema',
                };
                return (
                  <button
                    key={tpl}
                    type="button"
                    className={`template-pill ${
                      selectedTemplate === tpl
                        ? pipelineMode === 'epdf'
                          ? 'selected epdf'
                          : 'selected'
                        : ''
                    }`}
                    onClick={() => setSelectedTemplate(tpl)}
                  >
                    <div className="template-pill-id">
                      <span>{tpl}</span>
                      {selectedTemplate === tpl && (
                        <span
                          style={{
                            color:
                              pipelineMode === 'epdf' ? '#34d399' : '#38bdf8',
                          }}
                        >
                          ●
                        </span>
                      )}
                    </div>
                    <div className="template-pill-sub">{meta.arabic}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Drag & Drop PDF Input */}
          <div className="field-group">
            <label className="field-label">
              <span>PDF Documents (Single or Multiple)</span>
              <span>Max 50 MB / PDF</span>
            </label>
            <div
              className={`dropzone ${isDragging ? 'dragging' : ''}`}
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragging(false);
                handleAddFiles(e.dataTransfer.files);
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,application/pdf"
                multiple
                style={{ display: 'none' }}
                onChange={(e) => {
                  handleAddFiles(e.target.files);
                  e.target.value = '';
                }}
              />
              <div className="dropzone-icon">
                <svg
                  width="24"
                  height="24"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="12" y1="18" x2="12" y2="12" />
                  <line x1="9" y1="15" x2="15" y2="15" />
                </svg>
              </div>
              <div className="dropzone-title">
                Drop one or multiple PDF files here, or click to browse
              </div>
              <div className="dropzone-hint">
                {pipelineMode === 'epdf' ? (
                  <>
                    Requires electronic PDFs with selectable text — routes single
                    file to <code>/api/epdf/process</code> and multiple files to{' '}
                    <code>/api/epdf/batch</code>
                  </>
                ) : (
                  <>
                    Automatically routes single file to{' '}
                    <code>/process-pdf/</code> and multiple files to{' '}
                    <code>/batch-process-pdf/</code>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Staged Files List */}
          {files.length > 0 && (
            <div className="field-group">
              <div className="field-label">
                <span>Selected Files ({files.length})</span>
                <button
                  type="button"
                  onClick={() => setFiles([])}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#fb7185',
                    cursor: 'pointer',
                    fontSize: '0.74rem',
                    fontWeight: 600,
                  }}
                >
                  Clear All
                </button>
              </div>
              <div className="file-list">
                {files.map((file, idx) => (
                  <div key={`${file.name}-${idx}`} className="file-item">
                    <div className="file-item-left">
                      <span className="pdf-badge">PDF</span>
                      <div className="file-meta">
                        <div className="file-name" title={file.name}>
                          {file.name}
                        </div>
                        <div className="file-size">{formatBytes(file.size)}</div>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn-icon"
                      onClick={() => handleRemoveFile(idx)}
                      title="Remove file"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Raw Pages Option */}
          <label className="toggle-row" style={{ cursor: 'pointer' }}>
            <span>Include unmerged per-page output (raw_pages)</span>
            <input
              type="checkbox"
              className="checkbox-input"
              checked={includeRawPages}
              onChange={(e) => setIncludeRawPages(e.target.checked)}
            />
          </label>

          {errorMsg && (
            <div className="error-banner">
              <span>⚠️</span>
              <span>{errorMsg}</span>
            </div>
          )}

          <button
            type="submit"
            className={`btn-submit ${pipelineMode === 'epdf' ? 'epdf' : ''}`}
            disabled={isSubmitting || files.length === 0}
          >
            {isSubmitting ? (
              <>
                <span className="spinner" style={{ borderTopColor: '#fff' }} />
                {pipelineMode === 'epdf'
                  ? 'Uploading & Queuing e-PDFs...'
                  : 'Uploading & Queuing PDFs...'}
              </>
            ) : (
              <>
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                >
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                {pipelineMode === 'epdf'
                  ? files.length > 1
                    ? `Extract ${files.length} e-PDFs (${selectedTemplate.toUpperCase()})`
                    : `Extract e-PDF (${selectedTemplate.toUpperCase()})`
                  : files.length > 1
                  ? `Submit ${files.length} PDFs (${selectedTemplate.toUpperCase()})`
                  : `Submit PDF (${selectedTemplate.toUpperCase()})`}
              </>
            )}
          </button>
        </form>

        {/* Right Column: Live Progress & JSON Output Viewer */}
        <section className="panel-card results-panel">
          <div className="panel-header">
            <div className="panel-title">
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#10b981"
                strokeWidth="2.2"
              >
                <polyline points="16 18 22 12 16 6" />
                <polyline points="8 6 2 12 8 18" />
              </svg>
              Extracted JSON Results
            </div>

            {jobs.length > 0 && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={handleCopyJson}
                >
                  {copiedId === 'main' ? '✓ Copied JSON' : 'Copy JSON'}
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={handleDownloadJson}
                >
                  Download .JSON
                </button>
              </div>
            )}
          </div>

          {jobs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">
                <svg
                  width="28"
                  height="28"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <path d="M10 12l-2 2 2 2" />
                  <path d="M14 12l2 2-2 2" />
                </svg>
              </div>
              <div style={{ fontSize: '1.05rem', fontWeight: 700 }}>
                Ready to Extract Medical PDFs to JSON
              </div>
              <div
                style={{
                  fontSize: '0.85rem',
                  color: 'var(--text-secondary)',
                  maxWidth: 460,
                }}
              >
                Choose a clinic/hospital template from the dropdown, drop one or
                multiple PDF files on the left, and click{' '}
                <strong>Submit</strong> to stream live extraction progress and
                inspect each document&apos;s structured JSON output.
              </div>
            </div>
          ) : (
            <>
              {/* Batch / Job Progress Header */}
              <div className="batch-summary-bar">
                <div className="batch-summary-top">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    {progressStats.completedJobs + progressStats.errorJobs <
                      progressStats.totalJobs && <span className="spinner" />}
                    <span style={{ fontWeight: 700, fontSize: '0.92rem' }}>
                      {progressStats.completedJobs === progressStats.totalJobs
                        ? `All ${progressStats.totalJobs} PDF(s) Extracted Successfully`
                        : `Processing ${progressStats.totalJobs} PDF(s) with "${
                            jobs[0]?.template || selectedTemplate
                          }" template...`}
                    </span>
                  </div>
                  <div className="batch-stats">
                    {(jobs[0]?.pipeline || pipelineMode) && (
                      <span
                        className={`pipeline-badge ${
                          (jobs[0]?.pipeline || pipelineMode) === 'epdf'
                            ? 'epdf'
                            : 'ocr'
                        }`}
                      >
                        {(jobs[0]?.pipeline || pipelineMode) === 'epdf'
                          ? 'e-PDF'
                          : 'OCR'}
                      </span>
                    )}
                    <span className="stat-chip">
                      PDFs: {progressStats.completedJobs}/
                      {progressStats.totalJobs}
                    </span>
                    <span className="stat-chip">
                      Pages: {progressStats.donePages}/
                      {progressStats.totalPages} ({progressStats.percent}%)
                    </span>
                    {batchId && (
                      <span className="stat-chip" title={batchId}>
                        Batch: {batchId.slice(0, 8)}…
                      </span>
                    )}
                  </div>
                </div>
                <div className="progress-track">
                  <div
                    className="progress-fill"
                    style={{ width: `${progressStats.percent}%` }}
                  />
                </div>
              </div>

              {/* Per-PDF Tabs when multiple PDFs or single PDF */}
              <div className="job-tabs">
                {jobs.length > 1 && (
                  <button
                    type="button"
                    className={`job-tab ${activeTab === 'all' ? 'active' : ''}`}
                    onClick={() => setActiveTab('all')}
                  >
                    <span>All PDFs ({jobs.length} JSONs)</span>
                  </button>
                )}
                {jobs.map((job, idx) => (
                  <button
                    key={job.task_id}
                    type="button"
                    className={`job-tab ${
                      activeTab === job.task_id ||
                      (jobs.length === 1 && activeTab === 'all')
                        ? 'active'
                        : ''
                    }`}
                    onClick={() => setActiveTab(job.task_id)}
                  >
                    <span>
                      {idx + 1}. {job.filename || job.task_id.slice(0, 8)}
                    </span>
                    <span className={`status-pill ${job.status || 'queued'}`}>
                      {job.status === 'processing'
                        ? `${job.completed_pages || 0}/${job.page_count || '?'}p`
                        : job.status}
                    </span>
                  </button>
                ))}
              </div>

              {/* JSON Viewer Toolbar */}
              <div>
                <div className="json-toolbar">
                  <div className="view-mode-group">
                    <button
                      type="button"
                      className={`view-mode-btn ${
                        viewMode === 'json' ? 'active' : ''
                      }`}
                      onClick={() => setViewMode('json')}
                    >
                      Extracted JSON
                    </button>
                    <button
                      type="button"
                      className={`view-mode-btn ${
                        viewMode === 'full_job' ? 'active' : ''
                      }`}
                      onClick={() => setViewMode('full_job')}
                    >
                      Full Job Metadata + JSON
                    </button>
                    <button
                      type="button"
                      className={`view-mode-btn ${
                        viewMode === 'visual' ? 'active' : ''
                      }`}
                      onClick={() => setViewMode('visual')}
                    >
                      Visual Tables
                    </button>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {viewMode !== 'visual' && (
                      <input
                        type="text"
                        className="text-input mono"
                        style={{ width: 210, padding: '6px 10px', fontSize: '0.78rem' }}
                        placeholder="Filter / highlight key or value..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                      />
                    )}
                  </div>
                </div>

                {viewMode === 'visual' ? (
                  <StructuredPreview result={selectedJobObj?.result} />
                ) : (
                  <div className="json-container">
                    <SyntaxHighlightedJson
                      data={displayedJson}
                      searchTerm={searchTerm}
                    />
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  );
}

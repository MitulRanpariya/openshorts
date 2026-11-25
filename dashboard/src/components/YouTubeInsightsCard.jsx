import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw, ExternalLink } from 'lucide-react';
import { apiFetch } from '../lib/api';

// ClipForge Phase 6: connection state + performance of uploaded clips.
// Only metrics the official APIs return; a missing one says "unavailable".
const METRICS = [
    ['averageViewPercentage', 'avg % viewed'],
    ['averageViewDuration', 'avg view duration (s)'],
    ['views', 'views'],
    ['subscribersGained', 'subscribers gained'],
];

function fmt(v) {
    return v === null || v === undefined ? 'unavailable' : v;
}

export default function YouTubeInsightsCard() {
    const [status, setStatus] = useState(null);
    const [metric, setMetric] = useState(METRICS[0][0]);
    const [report, setReport] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        try {
            const read = async (url) => {
                const res = await apiFetch(url);
                const body = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(body.detail || `request failed (${res.status})`);
                return body;
            };
            const [s, r] = await Promise.all([
                read('/api/youtube/status'),
                read(`/api/youtube/insights?metric=${metric}`),
            ]);
            setStatus(s);
            setReport(r);
            setError('');
        } catch (e) {
            setError(e.message || String(e));
        }
    }, [metric]);

    useEffect(() => { load(); }, [load]);

    const refresh = async () => {
        setBusy(true); setError('');
        try {
            const res = await apiFetch('/api/youtube/analytics/refresh', { method: 'POST' });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(body.detail || 'refresh failed');
            await load();
        } catch (e) { setError(e.message); } finally { setBusy(false); }
    };

    return (
        <div className="card p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
                <div>
                    <p className="eyebrow">YOUTUBE · CLIPFORGE</p>
                    <p className="text-sm text-ink">
                        {status?.connected
                            ? <>Connected to <b>{status.channel || 'your channel'}</b> · uploads today {status.uploads_today}/{status.max_per_day} · auto-upload {status.auto_upload ? 'on' : 'off'} ({status.privacy})</>
                            : status?.client_configured
                                ? 'OAuth client set — run `python youtube_publisher.py auth` to connect.'
                                : 'Not connected — see docs/clipforge/USER_GUIDE.md (YouTube upload).'}
                    </p>
                </div>
                <button onClick={refresh} disabled={busy || !status?.connected} className="btn-quiet px-3 py-1.5 text-xs flex items-center gap-1">
                    {busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} refresh metrics
                </button>
            </div>
            {error && <p className="text-xs text-warn">{error}</p>}
            {report && (
                <div className="flex items-center gap-2 text-xs">
                    <span className="text-muted">compare by</span>
                    <select value={metric} onChange={(e) => setMetric(e.target.value)} className="bg-paper border border-rule rounded-input px-2 py-1">
                        {METRICS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                    </select>
                    <span className="text-muted">n = {report.sample_size}</span>
                </div>
            )}
            {report && report.sample_size > 0 && (
                <div className="space-y-2">
                    <p className="text-[11px] text-muted">{report.note}</p>
                    {Object.entries(report.groups || {}).map(([feature, groups]) => (
                        <div key={feature} className="text-xs">
                            <span className="eyebrow">{feature.toUpperCase()}</span>
                            <div className="flex flex-wrap gap-2 mt-1">
                                {Object.entries(groups).map(([label, g]) => (
                                    <span key={label} className={`px-2 py-0.5 rounded-full bg-paper3 ${g.comparable ? 'text-ink' : 'text-muted'}`}
                                        title={g.comparable ? '' : 'too few clips to compare'}>
                                        {label}: {g.average} (n={g.n})
                                    </span>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
            )}
            {report?.clips?.length > 0 && (
                <ul className="text-xs space-y-1 max-h-48 overflow-auto">
                    {report.clips.map((c) => (
                        <li key={c.url} className="flex items-center justify-between gap-2">
                            <a href={c.url} target="_blank" rel="noreferrer" className="truncate underline flex items-center gap-1">{c.title} <ExternalLink size={10} /></a>
                            <span className="text-muted shrink-0">{c.privacy} · views {fmt(c.metrics?.views)} · avg% {fmt(c.metrics?.analytics?.averageViewPercentage)}</span>
                        </li>
                    ))}
                </ul>
            )}
            {report && report.sample_size === 0 && (
                <p className="text-xs text-muted">No uploaded clips have this metric yet. Try another one above (views arrive first), or refresh later: YouTube reports watch-time with a delay of a day or two.</p>
            )}
        </div>
    );
}

// Settings store, data fetching and position sizing in one place.
import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

export const C = {
  bg: '#0B0F14', card: '#141A22', card2: '#1B232E', border: '#243040',
  text: '#E6EDF3', dim: '#8B98A9', green: '#2ECC71', red: '#FF5C5C',
  amber: '#F5B841', blue: '#4EA8FF',
};
export const gradeColor = (g) => ({ A: C.green, B: C.amber, C: C.dim }[g] || C.dim);
export const biasColor = (b) => ({ BULLISH: C.green, BEARISH: C.red, RANGEBOUND: C.amber }[b] || C.dim);
export const inr = (n, d = 2) =>
  n == null ? '—' : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d });

export const useSettings = create(
  persist(
    (set) => ({
      // "static": GitHub raw URL of the repo's /public folder. "api": your FastAPI server.
      mode: 'static',
      baseUrl: '',
      capital: 10000,
      riskPct: 1,
      accepted: false,
      set: (patch) => set(patch),
    }),
    { name: 'settings', storage: createJSONStorage(() => AsyncStorage) }
  )
);

const ROUTES = {
  plan: ['plan.json', '/api/plan'],
  bias: ['global-bias.json', '/api/global-bias'],
  scan: ['volume-breakouts.json', '/api/volume-breakouts'],
  perf: ['performance.json', '/api/performance'],
};

export async function getData(key) {
  const { mode, baseUrl } = useSettings.getState();
  if (!baseUrl) throw new Error('Set the data URL in Settings first.');
  const base = baseUrl.replace(/\/+$/, '');
  const [file, route] = ROUTES[key];
  const url = mode === 'api' ? base + route : `${base}/${file}?t=${Date.now()}`;
  const res = await fetch(url, { headers: { 'Cache-Control': 'no-cache' } });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
  return res.json();
}

// Same formula as POST /api/position-calculator, so it also works offline.
export function positionSize({ capital, entry, stop, riskPct, target }) {
  const rps = Math.abs(entry - stop);
  if (!capital || !entry || !stop || rps === 0) return null;
  const budget = (capital * riskPct) / 100;
  const byRisk = Math.floor(budget / rps);
  const byCash = Math.floor(capital / entry);
  const qty = Math.max(0, Math.min(byRisk, byCash));
  return {
    qty,
    used: qty * entry,
    maxLoss: qty * rps,
    budget,
    rps,
    limitedBy: byCash < byRisk ? 'capital' : 'risk',
    reward: target ? qty * Math.abs(target - entry) : null,
    rr: target ? Math.abs(target - entry) / rps : null,
  };
}

export const ago = (iso) => {
  if (!iso) return '';
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 60) return `${m} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
};

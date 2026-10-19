import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import PositionModal from '../components/PositionModal';
import { Badge, Card, Disclaimer, H, Screen, T, Updated } from '../components/ui';
import { C, getData, gradeColor, inr } from '../core';

const FILTERS = [
  ['ab', 'Grade A/B'],
  ['all', 'All 4 rules'],
  ['near', 'Near misses'],
];

function StockCard({ s, onPress }) {
  const up = s.day_chg >= 0;
  return (
    <Pressable onPress={onPress}>
      <Card accent={gradeColor(s.grade)}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
          <View>
            <T style={{ fontWeight: '800', fontSize: 16 }}>{s.symbol}</T>
            <T dim>{s.name}</T>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <T style={{ fontWeight: '700' }}>{inr(s.cmp)}</T>
            <Text style={{ color: up ? C.green : C.red, fontWeight: '700' }}>{up ? '+' : ''}{s.day_chg}%</Text>
          </View>
        </View>
        <View style={{ flexDirection: 'row', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
          <Badge label={`Grade ${s.grade}`} color={gradeColor(s.grade)} />
          <Badge label={s.vol_badge} color={C.blue} />
          <Badge label={`RSI ${s.rsi}`} color={C.dim} />
          <Badge label={`Win ${s.win_prob}%`} color={C.dim} />
        </View>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 }}>
          <T dim>SL <Text style={{ color: C.red }}>{inr(s.stop)}</Text></T>
          <T dim>T1 <Text style={{ color: C.green }}>{inr(s.t1)}</Text></T>
          <T dim>T2 <Text style={{ color: C.green }}>{inr(s.t2)}</Text></T>
        </View>
        {s.skip_reason && <T style={{ color: C.amber, marginTop: 6 }}>⚠️ {s.skip_reason}</T>}
        <T dim style={{ marginTop: 4, fontSize: 12 }}>Tap for position size & breakdown</T>
      </Card>
    </Pressable>
  );
}

export default function ScreenerScreen() {
  const [filter, setFilter] = useState('ab');
  const [sel, setSel] = useState(null);
  const q = useQuery({ queryKey: ['scan'], queryFn: () => getData('scan'), refetchInterval: 5 * 60 * 1000 });
  return (
    <>
      <Screen query={q}>
        {(d) => {
          const list =
            filter === 'near' ? d.near_misses
              : filter === 'all' ? d.candidates
                : d.candidates.filter((c) => c.grade !== 'C');
          return (
            <>
              <Updated iso={d.generated_at} />
              <View style={{ flexDirection: 'row', gap: 8, marginBottom: 12 }}>
                {FILTERS.map(([k, label]) => (
                  <Pressable key={k} onPress={() => setFilter(k)}
                    style={{ paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, backgroundColor: filter === k ? C.text : C.card }}>
                    <Text style={{ color: filter === k ? C.bg : C.text, fontWeight: '700' }}>{label}</Text>
                  </Pressable>
                ))}
              </View>
              {d.market_gate_open === false && (
                <Card accent={C.amber}><T>🚦 Market gate closed: all momentum buys are graded C today.</T></Card>
              )}
              <T dim style={{ marginBottom: 8 }}>
                Rules now: Vol ≥ {d.rules.vol_mult}x · RSI {d.rules.rsi_lo}–{d.rules.rsi_hi} · within {d.rules.near_high_pct}% of 20D high · above EMA20/50
                {d.volume_projected ? ' · volume projected (market open)' : ''}
              </T>
              {list.length === 0 && <Card><T>Nothing here today. No signal = no trade.</T></Card>}
              {list.map((s) => <StockCard key={s.symbol} s={s} onPress={() => setSel(s)} />)}
              <Disclaimer text={d.disclaimer} />
            </>
          );
        }}
      </Screen>
      <PositionModal stock={sel} onClose={() => setSel(null)} />
    </>
  );
}

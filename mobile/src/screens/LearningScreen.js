// Shows the self-learning loop: live vs backtest results and every rule change it made.
import { useQuery } from '@tanstack/react-query';
import { View } from 'react-native';
import { Badge, Card, H, Row, Screen, T, Updated } from '../components/ui';
import { C, getData } from '../core';

const Stats = ({ title, s }) => (
  <Card>
    <T style={{ fontWeight: '800' }}>{title}</T>
    {s.trades ? (
      <>
        <Row label="Trades graded" value={s.trades} />
        <Row label="Win rate" value={`${s.win_rate}%`} />
        <Row label="Avg result per trade" value={`${s.avg_r} R`} color={s.avg_r > 0 ? C.green : C.red} />
        <Row label="Total" value={`${s.total_r} R`} />
        {s.note && <T dim style={{ fontSize: 12 }}>{s.note}</T>}
      </>
    ) : (
      <T dim>No graded trades yet. Results appear 2 trading days after a signal.</T>
    )}
  </Card>
);

export default function LearningScreen() {
  const q = useQuery({ queryKey: ['perf'], queryFn: () => getData('perf'), refetchInterval: 15 * 60 * 1000 });
  return (
    <Screen query={q}>
      {(d) => (
        <>
          <Updated iso={d.generated_at} />
          <Card accent={C.blue}>
            <T>
              Every evening the engine grades each signal from 2 days ago, re-tests its rules on all results so far
              (recent and live results count more), and changes at most one step per rule per day.
            </T>
            <T dim style={{ marginTop: 6 }}>
              1R = the amount you planned to risk. +0.5R means you made half of your risk.
            </T>
          </Card>
          <Stats title="LIVE stock signals" s={d.live_stocks} />
          <Stats title="Backtest (~2 years)" s={d.backtest_stocks} />
          <Stats title="LIVE index views" s={d.index_views} />
          <Card>
            <T style={{ fontWeight: '800' }}>Bias model</T>
            <Row label="Live accuracy" value={d.bias_live.accuracy == null ? '—' : `${d.bias_live.accuracy}% (${d.bias_live.days} days)`} />
            {d.bias_walk_forward && (
              <Row label="Out-of-sample (500 days)" value={`${d.bias_walk_forward.directional_accuracy}% on ${d.bias_walk_forward.directional_calls} calls`} />
            )}
          </Card>

          <H>Model v{d.model.version}</H>
          <Card>
            <View style={{ flexDirection: 'row', gap: 6, marginBottom: 6 }}>
              <Badge label={d.model.ranker_on ? 'ML ranker ON' : 'ML ranker OFF'} color={d.model.ranker_on ? C.green : C.dim} />
              <Badge label={`${d.model.samples} samples`} />
            </View>
            <T dim>The ML ranker switches on only after it beats a simple base rate on unseen, later data.</T>
          </Card>

          <H>What it changed</H>
          {d.history.map((h) => (
            <Card key={h.version}>
              <T style={{ fontWeight: '700' }}>v{h.version} · {h.created_at.slice(0, 16).replace('T', ' ')}</T>
              <T dim>{h.note}</T>
            </Card>
          ))}

          {d.recent_live.length > 0 && <H>Recent live results</H>}
          {d.recent_live.map((r, i) => (
            <Row key={i} label={`${r.date} ${r.symbol}`} value={`${r.result} ${r.r > 0 ? '+' : ''}${r.r}R`} color={r.r > 0 ? C.green : C.red} />
          ))}
        </>
      )}
    </Screen>
  );
}

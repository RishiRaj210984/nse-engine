// Home tab: everything needed to decide in under a minute.
import { useQuery } from '@tanstack/react-query';
import { Text, View } from 'react-native';
import { Badge, Card, Disclaimer, H, Row, Screen, T, Updated } from '../components/ui';
import { C, biasColor, getData, gradeColor, inr } from '../core';

export default function PlanScreen() {
  const q = useQuery({ queryKey: ['plan'], queryFn: () => getData('plan'), refetchInterval: 5 * 60 * 1000 });
  return (
    <Screen query={q}>
      {(p) => (
        <>
          <Updated iso={p.generated_at} />
          <Card accent={biasColor(p.bias)}>
            <T dim>Plan for {p.session}</T>
            <Text style={{ color: biasColor(p.bias), fontSize: 26, fontWeight: '900', marginVertical: 4 }}>{p.bias}</Text>
            <T style={{ fontWeight: '700' }}>{p.headline}</T>
            <T dim style={{ marginTop: 6 }}>
              Bias model track record: {p.bias_track_record_pct ?? '—'}% correct on its directional calls (out-of-sample)
            </T>
          </Card>

          <H>Options</H>
          {p.options.map((o) => (
            <Card key={o.index}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <T style={{ fontWeight: '800' }}>{o.index} · {o.strategy}</T>
                <Badge label={`Exp ${o.expiry}`} />
              </View>
              <T style={{ marginVertical: 6, color: C.blue, fontWeight: '700' }}>{o.legs}</T>
              <Row label="When to enter" value="" />
              <T>{o.entry}</T>
              <Row label="Target" value="" />
              <T style={{ color: C.green }}>{o.target}</T>
              <Row label="Stop-loss" value="" />
              <T style={{ color: C.red }}>{o.stop_loss}</T>
              <View style={{ height: 8 }} />
              <Row label={`Max loss / lot (${o.lot_size})`} value={inr(o.max_loss, 0)} color={C.red} />
              <Row label="Max reward / lot" value={o.max_reward == null ? 'Open' : inr(o.max_reward, 0)} color={C.green} />
              <Row label="Reward : Risk" value={o.reward_risk ?? '—'} />
            </Card>
          ))}

          <H>Stocks (1–2 day)</H>
          {p.market_gate_open === false && (
            <Card accent={C.amber}>
              <T>🚦 Market gate CLOSED: Nifty is below its 50-DMA or down over 20 days. Momentum buys are paused.</T>
            </Card>
          )}
          {p.stocks.length === 0 ? (
            <Card><T>{p.no_trade_reason}</T></Card>
          ) : (
            p.stocks.map((s) => (
              <Card key={s.symbol} accent={gradeColor(s.grade)}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                  <T style={{ fontWeight: '800', fontSize: 16 }}>{s.symbol}</T>
                  <Badge label={`Grade ${s.grade} · ${s.win_prob}%`} color={gradeColor(s.grade)} />
                </View>
                <T style={{ marginTop: 6 }}>{s.action}</T>
              </Card>
            ))
          )}

          <H>Timeline (IST)</H>
          <Card>
            {p.timeline.map((t, i) => (
              <View key={i} style={{ marginBottom: 10 }}>
                <T style={{ color: C.amber, fontWeight: '700' }}>{t.when}</T>
                <T>{t.do}</T>
              </View>
            ))}
          </Card>

          <H>Golden rules</H>
          <Card>{p.golden_rules.map((r, i) => <T key={i}>• {r}</T>)}</Card>
          <Disclaimer text={p.disclaimer} />
        </>
      )}
    </Screen>
  );
}

// Dashboard: global ticker, bias breakdown, full options structures, market news.
import { useQuery } from '@tanstack/react-query';
import { Linking, Pressable, ScrollView, Text, View } from 'react-native';
import { Badge, Card, Disclaimer, H, Row, Screen, T, Updated } from '../components/ui';
import { C, biasColor, getData, inr } from '../core';

const Tick = ({ t }) => {
  const up = t.change >= 0;
  return (
    <View style={{ backgroundColor: C.card, borderRadius: 12, padding: 10, marginRight: 8, minWidth: 120, borderWidth: 1, borderColor: C.border }}>
      <T dim style={{ fontSize: 12 }}>{t.name}</T>
      <T style={{ fontWeight: '700' }}>{t.last.toLocaleString('en-IN')}</T>
      <Text style={{ color: up ? C.green : C.red, fontWeight: '700' }}>
        {up ? '▲' : '▼'} {Math.abs(t.change).toFixed(2)}{t.change_unit === '%' ? '%' : ' pts'}
      </Text>
    </View>
  );
};

export default function MarketScreen() {
  const q = useQuery({ queryKey: ['bias'], queryFn: () => getData('bias'), refetchInterval: 5 * 60 * 1000 });
  return (
    <Screen query={q}>
      {(d) => (
        <>
          <Updated iso={d.generated_at} />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 12 }}>
            {d.tickers.map((t) => <Tick key={t.symbol} t={t} />)}
          </ScrollView>

          <Card accent={biasColor(d.bias.label)}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <H style={{ marginBottom: 0 }}>Daily Bias · {d.target_session}</H>
              <Badge label={`${d.bias.label} ${d.bias.score > 0 ? '+' : ''}${d.bias.score}`} color={biasColor(d.bias.label)} />
            </View>
            <Row label="Model expects Nifty" value={`${d.bias.predicted_nifty_move_pct > 0 ? '+' : ''}${d.bias.predicted_nifty_move_pct}%`} />
            <Row label="Typical daily move" value={`±${d.bias.typical_daily_move_pct}%`} />
            <Row label="India VIX" value={d.bias.india_vix} />
            <Row label="Out-of-sample accuracy" value={`${d.model.directional_accuracy ?? '—'}% (${d.model.directional_calls} calls)`} />
            <Row label="US session used" value={d.us_session_used} />
            <T dim style={{ marginTop: 8 }}>What drove it:</T>
            {d.bias.components.slice(0, 5).map((c) => (
              <Row key={c.feature} label={`${c.label}: ${c.value}`}
                value={`${c.contribution_pct > 0 ? '+' : ''}${c.contribution_pct}%`}
                color={c.contribution_pct >= 0 ? C.green : C.red} />
            ))}
          </Card>

          {Object.values(d.options).map((o) => (
            <View key={o.index}>
              <H>{o.index} options · spot {o.spot.toLocaleString('en-IN')}</H>
              <T dim style={{ marginBottom: 6 }}>
                Expiry {o.expiry} · IV {o.iv_used_pct}% · expected move ±{o.expected_move} · S {o.levels.support} / R {o.levels.resistance}
              </T>
              {o.strategies.map((s) => (
                <Card key={s.name}>
                  <T style={{ fontWeight: '800' }}>{s.name}</T>
                  {s.legs.map((l, i) => (
                    <Row key={i} label={`${l.action} ${l.strike} ${l.type}`} value={`≈ ₹${l.est_premium}`}
                      color={l.action === 'BUY' ? C.green : C.red} />
                  ))}
                  <Row label="Max loss / lot" value={inr(s.max_loss, 0)} color={C.red} />
                  <Row label="Max reward / lot" value={s.max_reward == null ? 'Open' : inr(s.max_reward, 0)} color={C.green} />
                  <Row label="Breakeven" value={s.breakeven.join(' / ')} />
                  <T dim style={{ marginTop: 4 }}>Entry: {s.entry}</T>
                  <T dim>Target: {s.target}</T>
                  <T dim>SL: {s.stop_loss}</T>
                  {!!s.notes && <T dim style={{ fontStyle: 'italic' }}>{s.notes}</T>}
                </Card>
              ))}
              <T dim style={{ fontSize: 11, marginBottom: 8 }}>{o.premium_note}</T>
            </View>
          ))}

          <H>Market news · {d.news.summary.label}</H>
          {d.news.headlines.map((n, i) => (
            <Pressable key={i} onPress={() => n.link && Linking.openURL(n.link)}>
              <Card>
                <T>{n.title}</T>
                <T dim>{n.source} · sentiment {n.sentiment}</T>
              </Card>
            </Pressable>
          ))}
          {d.notes.map((n, i) => <T key={i} dim style={{ fontSize: 11 }}>• {n}</T>)}
          <Disclaimer text={d.disclaimer} />
        </>
      )}
    </Screen>
  );
}

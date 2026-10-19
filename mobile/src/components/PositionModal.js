import { useEffect, useState } from 'react';
import { Linking, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { C, gradeColor, inr, positionSize, useSettings } from '../core';
import { Badge, Card, H, Row, T } from './ui';

const Field = ({ label, value, onChange }) => (
  <View style={{ flex: 1, marginRight: 8 }}>
    <T dim>{label}</T>
    <TextInput style={s.input} keyboardType="decimal-pad" value={String(value)} onChangeText={onChange} />
  </View>
);

export default function PositionModal({ stock, onClose }) {
  const { capital, riskPct } = useSettings();
  const [f, setF] = useState({});
  useEffect(() => {
    if (stock) setF({ capital, riskPct, entry: stock.entry, stop: stock.stop });
  }, [stock]);
  if (!stock) return null;
  const n = (k) => parseFloat(f[k]) || 0;
  const r = positionSize({ capital: n('capital'), entry: n('entry'), stop: n('stop'), riskPct: n('riskPct'), target: stock.t1 });
  const ck = stock.checks || {};

  return (
    <Modal visible animationType="slide" onRequestClose={onClose} transparent>
      <View style={s.backdrop}>
        <View style={s.sheet}>
          <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 40 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <H style={{ marginBottom: 0 }}>{stock.symbol}</H>
              <Badge label={`Grade ${stock.grade}`} color={gradeColor(stock.grade)} big />
            </View>
            <T dim>{stock.name}</T>

            <Card style={{ marginTop: 12, backgroundColor: C.card2 }} accent={gradeColor(stock.grade)}>
              <T style={{ fontWeight: '700' }}>What to do</T>
              <T>{stock.action}</T>
            </Card>

            <H>Position size</H>
            <View style={{ flexDirection: 'row' }}>
              <Field label="Capital ₹" value={f.capital} onChange={(v) => setF({ ...f, capital: v })} />
              <Field label="Risk %" value={f.riskPct} onChange={(v) => setF({ ...f, riskPct: v })} />
            </View>
            <View style={{ flexDirection: 'row', marginTop: 8 }}>
              <Field label="Entry ₹" value={f.entry} onChange={(v) => setF({ ...f, entry: v })} />
              <Field label="Stop-loss ₹" value={f.stop} onChange={(v) => setF({ ...f, stop: v })} />
            </View>
            {r && (
              <Card style={{ marginTop: 12, backgroundColor: C.card2 }}>
                <Text style={s.big}>Buy {r.qty} shares</Text>
                <Row label="Capital used" value={inr(r.used, 0)} />
                <Row label="Max loss if SL hits" value={inr(r.maxLoss, 0)} color={C.red} />
                <Row label="Profit at T1" value={inr(r.reward, 0)} color={C.green} />
                <Row label="Reward : Risk (T1)" value={r.rr?.toFixed(2)} />
                <T dim style={{ marginTop: 4 }}>
                  Limited by {r.limitedBy}. Excludes brokerage, STT and slippage.
                </T>
              </Card>
            )}

            <H>Why it was picked</H>
            <Card>
              <Row label={`Volume surge ${ck.volume ? '✅' : '❌'}`} value={`${stock.vol_ratio}x avg${stock.volume_projected ? ' (proj.)' : ''}`} />
              <Row label={`RSI 14 ${ck.momentum ? '✅' : '❌'}`} value={stock.rsi} />
              <Row label={`Above EMA20 & 50 ${ck.trend ? '✅' : '❌'}`} value={`${stock.ema20} / ${stock.ema50}`} />
              <Row label={`Near 20-day high ${ck.breakout ? '✅' : '❌'}`} value={`${stock.dist_high_pct}% below ${stock.high20}`} />
              <Row label="ATR (daily range)" value={inr(stock.atr)} />
              <Row label="Learned win probability" value={`${stock.win_prob}%`} />
              <Row label="Expected value per trade" value={`${stock.expected_r} R`} color={stock.expected_r > 0 ? C.green : C.red} />
            </Card>

            <H>Levels</H>
            <Card>
              <Row label="Entry" value={inr(stock.entry)} />
              <Row label="Stop-loss" value={inr(stock.stop)} color={C.red} />
              <Row label="Target 1 (R1 / ATR)" value={inr(stock.t1)} color={C.green} />
              <Row label="Target 2 (R2 / ATR)" value={inr(stock.t2)} color={C.green} />
              <T dim style={{ marginTop: 6 }}>{stock.trail_rule}</T>
            </Card>

            {stock.news?.length > 0 && (
              <>
                <H>News ({stock.news_sentiment?.label})</H>
                {stock.news.map((n, i) => (
                  <Pressable key={i} onPress={() => n.link && Linking.openURL(n.link)}>
                    <Card>
                      <T>{n.title}</T>
                      <T dim>{n.source} · sentiment {n.sentiment}</T>
                    </Card>
                  </Pressable>
                ))}
              </>
            )}
            <Pressable onPress={onClose} style={s.close}>
              <Text style={{ color: C.bg, fontWeight: '800' }}>Close</Text>
            </Pressable>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: '#000a', justifyContent: 'flex-end' },
  sheet: { backgroundColor: C.bg, borderTopLeftRadius: 20, borderTopRightRadius: 20, maxHeight: '92%', borderWidth: 1, borderColor: C.border },
  input: { backgroundColor: C.card, color: C.text, borderRadius: 10, padding: 10, fontSize: 16, borderWidth: 1, borderColor: C.border, marginTop: 4 },
  big: { color: C.text, fontSize: 24, fontWeight: '800', marginBottom: 6 },
  close: { backgroundColor: C.text, padding: 14, borderRadius: 12, alignItems: 'center', marginTop: 10 },
});

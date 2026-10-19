import { useQueryClient } from '@tanstack/react-query';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { Card, Disclaimer, H, T } from '../components/ui';
import { C, useSettings } from '../core';

const input = { backgroundColor: C.card, color: C.text, borderRadius: 10, padding: 12, borderWidth: 1, borderColor: C.border, marginTop: 6, marginBottom: 12 };

export default function SettingsScreen() {
  const st = useSettings();
  const qc = useQueryClient();
  return (
    <ScrollView style={{ flex: 1, backgroundColor: C.bg }} contentContainerStyle={{ padding: 14 }}>
      <H>Data source</H>
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
        {[['static', 'Cloud (GitHub)'], ['api', 'Live API']].map(([k, label]) => (
          <Pressable key={k} onPress={() => st.set({ mode: k })}
            style={{ flex: 1, padding: 12, borderRadius: 10, alignItems: 'center', backgroundColor: st.mode === k ? C.text : C.card }}>
            <Text style={{ color: st.mode === k ? C.bg : C.text, fontWeight: '700' }}>{label}</Text>
          </Pressable>
        ))}
      </View>
      <T dim>
        {st.mode === 'static'
          ? 'Paste: https://raw.githubusercontent.com/<you>/<repo>/main/docs'
          : 'Paste your server, e.g. http://192.168.1.20:8000'}
      </T>
      <TextInput style={input} autoCapitalize="none" autoCorrect={false} value={st.baseUrl}
        onChangeText={(v) => st.set({ baseUrl: v.trim() })} placeholder="Data URL" placeholderTextColor={C.dim} />

      <H>Position sizing defaults</H>
      <T dim>Capital (₹)</T>
      <TextInput style={input} keyboardType="numeric" value={String(st.capital)} onChangeText={(v) => st.set({ capital: parseFloat(v) || 0 })} />
      <T dim>Max risk per trade (%)</T>
      <TextInput style={input} keyboardType="decimal-pad" value={String(st.riskPct)} onChangeText={(v) => st.set({ riskPct: parseFloat(v) || 0 })} />

      <Pressable onPress={() => qc.invalidateQueries()} style={{ backgroundColor: C.blue, padding: 14, borderRadius: 12, alignItems: 'center' }}>
        <Text style={{ color: C.bg, fontWeight: '800' }}>Save & refresh</Text>
      </Pressable>
      <Card style={{ marginTop: 16 }}>
        <T dim>Updates: pre-market ~08:00 IST and after close ~16:30 IST on weekdays. Pull down on any tab to refresh.</T>
      </Card>
      <Disclaimer />
    </ScrollView>
  );
}

import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { C, ago } from '../core';

export const Card = ({ children, style, accent }) => (
  <View style={[s.card, accent && { borderLeftColor: accent, borderLeftWidth: 4 }, style]}>{children}</View>
);

export const Badge = ({ label, color = C.blue, big }) => (
  <View style={[s.badge, { borderColor: color, backgroundColor: color + '22' }, big && s.badgeBig]}>
    <Text style={[s.badgeText, { color }, big && { fontSize: 18 }]}>{label}</Text>
  </View>
);

export const Row = ({ label, value, color }) => (
  <View style={s.row}>
    <Text style={s.dim}>{label}</Text>
    <Text style={[s.val, color && { color }]}>{value}</Text>
  </View>
);

export const H = ({ children, style }) => <Text style={[s.h, style]}>{children}</Text>;
export const T = ({ children, style, dim }) => <Text style={[dim ? s.dim : s.t, style]}>{children}</Text>;

export function Screen({ query, children }) {
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: C.bg }}
      contentContainerStyle={{ padding: 14, paddingBottom: 40 }}
      refreshControl={
        <RefreshControl refreshing={query.isRefetching} onRefresh={query.refetch} tintColor={C.text} />
      }>
      {query.isLoading && <ActivityIndicator color={C.text} style={{ marginTop: 40 }} />}
      {query.error && (
        <Card accent={C.red}>
          <T>Couldn't load data</T>
          <T dim>{String(query.error.message)}</T>
          <T dim style={{ marginTop: 6 }}>Pull down to retry, or check Settings → Data URL.</T>
        </Card>
      )}
      {query.data && children(query.data)}
    </ScrollView>
  );
}

export const Updated = ({ iso }) => <T dim style={{ marginBottom: 10 }}>Updated {ago(iso)}</T>;

export const Disclaimer = ({ text }) => (
  <T dim style={{ fontSize: 11, marginTop: 16, lineHeight: 16 }}>
    ⚠️ {text || 'Research tool only. Not SEBI-registered advice. No returns guaranteed.'}
  </T>
);

const s = StyleSheet.create({
  card: { backgroundColor: C.card, borderRadius: 14, padding: 14, marginBottom: 12, borderWidth: 1, borderColor: C.border },
  badge: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3, alignSelf: 'flex-start' },
  badgeBig: { paddingHorizontal: 16, paddingVertical: 6 },
  badgeText: { fontWeight: '700', fontSize: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3 },
  dim: { color: C.dim, fontSize: 13 },
  val: { color: C.text, fontSize: 14, fontWeight: '600' },
  h: { color: C.text, fontSize: 18, fontWeight: '800', marginBottom: 6 },
  t: { color: C.text, fontSize: 14, lineHeight: 20 },
});

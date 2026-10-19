import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { DarkTheme, NavigationContainer } from '@react-navigation/native';
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { AppState, Pressable, ScrollView, Text } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { C, useSettings } from './src/core';
import LearningScreen from './src/screens/LearningScreen';
import MarketScreen from './src/screens/MarketScreen';
import PlanScreen from './src/screens/PlanScreen';
import ScreenerScreen from './src/screens/ScreenerScreen';
import SettingsScreen from './src/screens/SettingsScreen';

const qc = new QueryClient({ defaultOptions: { queries: { retry: 2, staleTime: 60 * 1000 } } });
const Tab = createBottomTabNavigator();
const theme = { ...DarkTheme, colors: { ...DarkTheme.colors, background: C.bg, card: C.card, text: C.text, border: C.border, primary: C.blue } };
const icon = (e) => () => <Text style={{ fontSize: 18 }}>{e}</Text>;

function DisclaimerGate() {
  const set = useSettings((s) => s.set);
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: C.bg }}>
      <ScrollView contentContainerStyle={{ padding: 22 }}>
        <Text style={{ color: C.text, fontSize: 24, fontWeight: '900', marginBottom: 12 }}>Before you start</Text>
        {[
          'This app gives algorithmic research insights from technical rules and statistics. It is NOT investment advice.',
          'It is not a SEBI-registered investment adviser or research analyst tool.',
          'No returns are guaranteed. SEBI found about 9 in 10 retail F&O traders lose money.',
          'Option premiums shown are estimates. Always confirm prices and margins with your broker.',
          'Use a stop-loss on every trade and never risk money you cannot afford to lose.',
        ].map((t, i) => (
          <Text key={i} style={{ color: C.text, fontSize: 15, lineHeight: 22, marginBottom: 10 }}>• {t}</Text>
        ))}
        <Pressable onPress={() => set({ accepted: true })} style={{ backgroundColor: C.text, padding: 16, borderRadius: 12, alignItems: 'center', marginTop: 12 }}>
          <Text style={{ color: C.bg, fontWeight: '800', fontSize: 16 }}>I understand</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

export default function App() {
  const accepted = useSettings((s) => s.accepted);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => focusManager.setFocused(s === 'active'));
    return () => sub.remove();
  }, []);
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={qc}>
        <StatusBar style="light" />
        {!accepted ? (
          <DisclaimerGate />
        ) : (
          <NavigationContainer theme={theme}>
            <Tab.Navigator screenOptions={{ headerStyle: { backgroundColor: C.bg }, headerTintColor: C.text, tabBarActiveTintColor: C.text }}>
              <Tab.Screen name="Plan" component={PlanScreen} options={{ title: "Today's Plan", tabBarIcon: icon('🎯') }} />
              <Tab.Screen name="Market" component={MarketScreen} options={{ tabBarIcon: icon('🌍') }} />
              <Tab.Screen name="Stocks" component={ScreenerScreen} options={{ tabBarIcon: icon('📈') }} />
              <Tab.Screen name="Learning" component={LearningScreen} options={{ tabBarIcon: icon('🧠') }} />
              <Tab.Screen name="Settings" component={SettingsScreen} options={{ tabBarIcon: icon('⚙️') }} />
            </Tab.Navigator>
          </NavigationContainer>
        )}
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

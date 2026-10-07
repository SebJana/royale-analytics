import { useCallback, useState } from "react";
import { Routes, Route } from "react-router-dom";
import HomePage from "./pages/home";
import PlayerLayout from "./pages/player/layout";
import PlayerDecks from "./pages/player/decks";
import PlayerBattles from "./pages/player/battles";
import PlayerCards from "./pages/player/cards";
import PlayerPlots from "./pages/player/plots";
import { useCardImagePreload } from "./hooks/useCardImagePreload";
import { StorageSettingsContext } from "./contexts/StorageSettingsContext";
import { StorageBanner } from "./components/storageBanner/storageBanner";
import { StorageSettings } from "./components/storageSettings/storageSettings";
import { SiteFooter } from "./components/siteFooter/siteFooter";
import "./App.css";

function App() {
  useCardImagePreload();

  const [storageSettingsOpen, setStorageSettingsOpen] = useState(false);
  const openStorageSettings = useCallback(
    () => setStorageSettingsOpen(true),
    [],
  );

  return (
    <StorageSettingsContext.Provider value={openStorageSettings}>
      <main className="main-container">
        <Routes>
          <Route path="/" element={<HomePage />} />

          <Route path="/player/:playerTag" element={<PlayerLayout />}>
            <Route index element={<PlayerBattles />} />
            <Route path="battles" element={<PlayerBattles />} />
            <Route path="decks" element={<PlayerDecks />} />
            <Route path="cards" element={<PlayerCards />} />
            <Route path="plots" element={<PlayerPlots />} />
          </Route>
        </Routes>
      </main>
      <SiteFooter />
      <StorageBanner />
      <StorageSettings
        open={storageSettingsOpen}
        onClose={() => setStorageSettingsOpen(false)}
      />
    </StorageSettingsContext.Provider>
  );
}

export default App;

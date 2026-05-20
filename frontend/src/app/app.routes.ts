import { Routes } from '@angular/router';

import { SettingsComponent } from './pages/settings/settings.component';
import { GamesComponent } from './pages/games/games.component';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'games' },
  { path: 'games', component: GamesComponent },
  { path: 'settings', component: SettingsComponent },
  // Phase 3 will add: library.
];

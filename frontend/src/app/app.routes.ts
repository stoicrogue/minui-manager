import { Routes } from '@angular/router';

import { SettingsComponent } from './pages/settings/settings.component';
import { GamesComponent } from './pages/games/games.component';
import { LibraryComponent } from './pages/library/library.component';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'games' },
  { path: 'games', component: GamesComponent },
  { path: 'library', component: LibraryComponent },
  { path: 'settings', component: SettingsComponent },
];

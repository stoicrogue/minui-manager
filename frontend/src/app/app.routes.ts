import { Routes } from '@angular/router';

import { SettingsComponent } from './pages/settings/settings.component';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'settings' },
  { path: 'settings', component: SettingsComponent },
  // Phase 2 will add: games (SD card view), library, archive.
];

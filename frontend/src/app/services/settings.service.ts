import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export type SDCardStatus = 'not_set' | 'not_found' | 'invalid' | 'ok';

export interface AppSettings {
  sd_card_path: string | null;
  boxart_target_width: number;
  boxart_target_height: number;
  boxart_resize_strategy: 'cover' | 'contain' | 'stretch';
  max_games_total: number | null;
  archive_on_remove: boolean;
  steamgriddb_api_key: string | null;
}

export interface SettingsPatch {
  sd_card_path?: string | null;
  boxart_target_width?: number;
  boxart_target_height?: number;
  boxart_resize_strategy?: 'cover' | 'contain' | 'stretch';
  max_games_total?: number | null;
  archive_on_remove?: boolean;
  steamgriddb_api_key?: string | null;
}

export interface SDCardStatusResponse {
  status: SDCardStatus;
  path: string | null;
  missing_markers: string[];
  detail: string;
}

/**
 * Wraps the FastAPI settings + SD card status endpoints.
 * All requests go through the dev proxy (proxy.conf.json) in development
 * and through the same origin in production.
 */
@Injectable({ providedIn: 'root' })
export class SettingsService {
  private readonly http = inject(HttpClient);

  getSettings(): Observable<AppSettings> {
    return this.http.get<AppSettings>('/api/settings');
  }

  updateSettings(patch: SettingsPatch): Observable<AppSettings> {
    return this.http.patch<AppSettings>('/api/settings', patch);
  }

  getSDCardStatus(): Observable<SDCardStatusResponse> {
    return this.http.get<SDCardStatusResponse>('/api/sdcard/status');
  }
}

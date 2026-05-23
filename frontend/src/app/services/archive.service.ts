import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { ArchivedGame } from './sdcard.service';

export interface RestoreSaveResult {
  restored: string[];
  archive_path: string;
  game_folder_name: string;
  display_name: string;
  system_code: string;
}

@Injectable({ providedIn: 'root' })
export class ArchiveService {
  private readonly http = inject(HttpClient);

  list(limit?: number): Observable<{ archived: ArchivedGame[] }> {
    const params: Record<string, string | number> = {};
    if (limit !== undefined) params['limit'] = limit;
    return this.http.get<{ archived: ArchivedGame[] }>('/api/archive', { params });
  }

  /** Copy the archived save file(s) back onto the SD card. The game must
   * already be on the card — send it from the library first. */
  restoreSaveToCard(archiveId: number): Observable<{ restored: RestoreSaveResult }> {
    return this.http.post<{ restored: RestoreSaveResult }>(
      `/api/archive/${archiveId}/restore-save-to-card`,
      {},
    );
  }

  /** Permanently delete an archive entry (DB row + on-disk save bundle). */
  delete(archiveId: number): Observable<{ deleted: ArchivedGame }> {
    return this.http.delete<{ deleted: ArchivedGame }>(`/api/archive/${archiveId}`);
  }
}

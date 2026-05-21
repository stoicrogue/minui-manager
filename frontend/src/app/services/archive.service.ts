import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { ArchivedGame } from './sdcard.service';
import { LibraryGame } from './library.service';

@Injectable({ providedIn: 'root' })
export class ArchiveService {
  private readonly http = inject(HttpClient);

  list(limit?: number): Observable<{ archived: ArchivedGame[] }> {
    const params: Record<string, string | number> = {};
    if (limit !== undefined) params['limit'] = limit;
    return this.http.get<{ archived: ArchivedGame[] }>('/api/archive', { params });
  }

  restoreToLibrary(archiveId: number): Observable<{ library_game: LibraryGame }> {
    return this.http.post<{ library_game: LibraryGame }>(
      `/api/archive/${archiveId}/restore-to-library`,
      {},
    );
  }

  /** Permanently delete an archived game (DB row + on-disk bundle). */
  delete(archiveId: number): Observable<{ deleted: ArchivedGame }> {
    return this.http.delete<{ deleted: ArchivedGame }>(`/api/archive/${archiveId}`);
  }
}

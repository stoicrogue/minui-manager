import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatCheckboxModule } from '@angular/material/checkbox';

import { LibraryGame, LibraryService } from '../../services/library.service';
import { BoxartService } from '../../services/boxart.service';
import { ArchiveService } from '../../services/archive.service';
import { ArchivedGame } from '../../services/sdcard.service';
import { collectFilesFromDrop } from '../../services/drop-folder.util';
import { UploadDialogComponent } from './upload-dialog.component';
import { BoxartPickerDialogComponent } from './boxart-picker-dialog.component';
import { SendToDeviceDialogComponent } from './send-to-device-dialog.component';
import { DeleteArchiveDialogComponent } from './delete-archive-dialog.component';

@Component({
  selector: 'app-library-page',
  standalone: true,
  imports: [
    CommonModule,
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatChipsModule,
    MatDialogModule,
    MatSnackBarModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatCheckboxModule,
  ],
  templateUrl: './library.component.html',
  styleUrl: './library.component.scss',
})
export class LibraryComponent implements OnInit {
  private readonly api = inject(LibraryService);
  private readonly boxart = inject(BoxartService);
  private readonly archive = inject(ArchiveService);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);

  readonly games = signal<LibraryGame[]>([]);
  readonly loading = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);
  readonly activeFilter = signal<string | null>(null); // system_code filter
  readonly dragOver = signal<boolean>(false);
  // Per-game cache-buster bumped whenever box art is re-selected. The image
  // URL is otherwise stable, so without this the browser keeps the old PNG.
  readonly boxArtVersion = signal<Record<number, number>>({});
  // Selection state: ids of games picked for the next "Send to device" sync.
  readonly selectedIds = signal<Set<number>>(new Set());
  readonly selectedCount = computed(() => this.selectedIds().size);
  readonly hasSelection = computed(() => this.selectedCount() > 0);
  readonly archived = signal<ArchivedGame[]>([]);
  readonly restoringId = signal<number | null>(null);
  readonly archiveOpen = signal<boolean>(false);

  readonly systemFilters = computed(() => {
    const codes = new Set(this.games().map((g) => g.system_code));
    return Array.from(codes).sort();
  });

  readonly visibleGames = computed(() => {
    const filter = this.activeFilter();
    return filter ? this.games().filter((g) => g.system_code === filter) : this.games();
  });

  ngOnInit(): void {
    this.refresh();
    this.refreshArchive();
  }

  refreshArchive(): void {
    this.archive.list(20).subscribe({
      next: (r) => this.archived.set(r.archived),
      error: () => {
        // Archive listing isn't critical — sidebar just stays empty.
      },
    });
  }

  toggleArchivePanel(): void {
    this.archiveOpen.update((v) => !v);
    if (this.archiveOpen()) this.refreshArchive();
  }

  openDeleteArchive(item: ArchivedGame): void {
    const ref = this.dialog.open(DeleteArchiveDialogComponent, {
      data: { item },
      maxWidth: '90vw',
      autoFocus: false,
    });
    ref.afterClosed().subscribe((result) => {
      if (result?.deleted) {
        this.snack.open(
          `Deleted archived ${item.display_name} (${item.system_code}).`,
          undefined,
          { duration: 3000 },
        );
        this.refreshArchive();
      }
    });
  }

  restoreFromArchive(item: ArchivedGame): void {
    this.restoringId.set(item.id);
    this.archive.restoreToLibrary(item.id).subscribe({
      next: (r) => {
        this.restoringId.set(null);
        const g = r.library_game;
        this.snack.open(
          `${g.display_name} restored to the library.`,
          undefined,
          { duration: 3000 },
        );
        this.refresh();
      },
      error: (err) => {
        this.restoringId.set(null);
        this.snack.open(
          `Restore failed: ${err.error?.detail ?? err.message ?? 'unknown error'}`,
          'Dismiss',
          { duration: 5000 },
        );
      },
    });
  }

  refresh(): void {
    this.loading.set(true);
    this.errorMessage.set(null);
    this.api.list().subscribe({
      next: (l) => {
        this.games.set(l.games);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.errorMessage.set(err.message ?? 'Failed to load library.');
      },
    });
  }

  openUpload(initialFiles?: File[]): void {
    const ref = this.dialog.open(UploadDialogComponent, {
      data: initialFiles && initialFiles.length > 0 ? { initialFiles } : null,
      maxWidth: '90vw',
      autoFocus: false,
    });
    ref.afterClosed().subscribe((result) => {
      if (result?.confirmed) {
        const g = result.game as LibraryGame;
        this.snack.open(`Added ${g.display_name} (${g.system_code}). Finding box art…`, undefined, {
          duration: 2500,
        });
        // Chain: open the box-art picker for the freshly added entry.
        this.openBoxartPicker(g, { fromUpload: true });
      }
    });
  }

  openBoxartPicker(game: LibraryGame, options: { fromUpload?: boolean } = {}): void {
    const ref = this.dialog.open(BoxartPickerDialogComponent, {
      data: { game },
      maxWidth: '95vw',
      autoFocus: false,
    });
    ref.afterClosed().subscribe((result) => {
      if (result?.selected) {
        this.snack.open(`Box art set for ${game.display_name}.`, undefined, {
          duration: 2000,
        });
        // Bump this game's cache-buster so <img src> changes and the browser
        // re-fetches the PNG instead of serving the stale cached copy.
        this.boxArtVersion.update((v) => ({ ...v, [game.id]: Date.now() }));
      } else if (options.fromUpload) {
        // User skipped the picker right after upload — fine, the library
        // entry still exists, they can come back to it later.
      }
      this.refresh();
    });
  }

  remove(game: LibraryGame): void {
    if (!confirm(`Remove "${game.display_name}" from the library?`)) {
      return;
    }
    this.api.remove(game.id).subscribe({
      next: () => {
        this.snack.open(`Removed ${game.display_name}.`, undefined, { duration: 2000 });
        this.refresh();
      },
      error: (err) => {
        this.snack.open(`Delete failed: ${err.message}`, 'Dismiss', { duration: 5000 });
      },
    });
  }

  setFilter(code: string | null): void {
    this.activeFilter.set(code);
  }

  isSelected(game: LibraryGame): boolean {
    return this.selectedIds().has(game.id);
  }

  toggleSelected(game: LibraryGame, event?: Event): void {
    event?.stopPropagation();
    this.selectedIds.update((s) => {
      const next = new Set(s);
      if (next.has(game.id)) next.delete(game.id);
      else next.add(game.id);
      return next;
    });
  }

  clearSelection(): void {
    this.selectedIds.set(new Set());
  }

  selectAllVisible(): void {
    this.selectedIds.set(new Set(this.visibleGames().map((g) => g.id)));
  }

  openSendToDevice(): void {
    const ids = this.selectedIds();
    const picked = this.games().filter((g) => ids.has(g.id));
    if (picked.length === 0) return;
    const ref = this.dialog.open(SendToDeviceDialogComponent, {
      data: { games: picked },
      maxWidth: '95vw',
      autoFocus: false,
      disableClose: true,
    });
    ref.afterClosed().subscribe((result) => {
      if (!result?.cancelled && (result?.ok ?? 0) > 0) {
        const okN = result.ok;
        const errN = result.errors ?? 0;
        const msg =
          errN > 0
            ? `Sent ${okN} games, ${errN} failed.`
            : `Sent ${okN} game${okN === 1 ? '' : 's'} to the card.`;
        this.snack.open(msg, undefined, { duration: 3500 });
        this.clearSelection();
      }
    });
  }

  // Drag-and-drop on the empty area / whole page → opens the upload dialog
  // pre-loaded with the dropped file.
  onPageDragOver(event: DragEvent): void {
    if (event.dataTransfer?.types.includes('Files')) {
      event.preventDefault();
      this.dragOver.set(true);
    }
  }

  onPageDragLeave(event: DragEvent): void {
    // Only clear when leaving the page boundary, not when moving between children.
    if (event.target === event.currentTarget) {
      this.dragOver.set(false);
    }
  }

  async onPageDrop(event: DragEvent): Promise<void> {
    event.preventDefault();
    this.dragOver.set(false);
    // Use webkitGetAsEntry to expand a dropped folder into its files,
    // so dragging a multi-disk game folder onto the page works the same
    // as opening the dialog and picking the files manually.
    const items = event.dataTransfer?.items;
    let files: File[] = [];
    if (items && items.length > 0 && (items[0] as any).webkitGetAsEntry) {
      files = await collectFilesFromDrop(items);
    } else {
      files = Array.from(event.dataTransfer?.files ?? []);
    }
    if (files.length > 0) {
      this.openUpload(files);
    }
  }

  prettySize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  /** Cache-buster: stable across page loads (uses game.id), but bumped to
   * a fresh timestamp by openBoxartPicker after a successful re-selection
   * so the browser actually re-fetches the new PNG. */
  boxArtUrl(game: LibraryGame): string {
    const version = this.boxArtVersion()[game.id] ?? game.id;
    return this.boxart.libraryBoxArtUrl(game.id, version);
  }
}

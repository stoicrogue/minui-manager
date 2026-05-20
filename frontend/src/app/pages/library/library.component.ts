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

import { LibraryGame, LibraryService } from '../../services/library.service';
import { BoxartService } from '../../services/boxart.service';
import { UploadDialogComponent } from './upload-dialog.component';
import { BoxartPickerDialogComponent } from './boxart-picker-dialog.component';

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
  ],
  templateUrl: './library.component.html',
  styleUrl: './library.component.scss',
})
export class LibraryComponent implements OnInit {
  private readonly api = inject(LibraryService);
  private readonly boxart = inject(BoxartService);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);

  readonly games = signal<LibraryGame[]>([]);
  readonly loading = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);
  readonly activeFilter = signal<string | null>(null); // system_code filter
  readonly dragOver = signal<boolean>(false);

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

  openUpload(initialFile?: File): void {
    const ref = this.dialog.open(UploadDialogComponent, {
      data: initialFile ? { initialFile } : null,
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

  onPageDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      this.openUpload(file);
    }
  }

  prettySize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  /** Cache-buster keyed off the row id so a re-selection forces a re-fetch. */
  boxArtUrl(game: LibraryGame): string {
    return this.boxart.libraryBoxArtUrl(game.id, game.id);
  }
}

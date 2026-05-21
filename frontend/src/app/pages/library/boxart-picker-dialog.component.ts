import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import {
  MAT_DIALOG_DATA,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatChipsModule } from '@angular/material/chips';

import { BoxartCandidate, BoxartSearchResponse, BoxartService } from '../../services/boxart.service';
import { LibraryGame } from '../../services/library.service';

export interface BoxartPickerData {
  game: LibraryGame;
}

@Component({
  selector: 'app-boxart-picker-dialog',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatChipsModule,
  ],
  templateUrl: './boxart-picker-dialog.component.html',
  styleUrl: './boxart-picker-dialog.component.scss',
})
export class BoxartPickerDialogComponent implements OnInit {
  private readonly api = inject(BoxartService);
  private readonly dialogRef = inject(MatDialogRef<BoxartPickerDialogComponent>);
  readonly data = inject<BoxartPickerData>(MAT_DIALOG_DATA);

  readonly searching = signal<boolean>(true);
  readonly selecting = signal<string | null>(null); // source_url being selected
  readonly uploading = signal<boolean>(false);
  readonly uploadDragOver = signal<boolean>(false);
  readonly result = signal<BoxartSearchResponse | null>(null);
  readonly errorMessage = signal<string | null>(null);
  readonly queryOverride = signal<string>('');

  readonly busy = computed(() => this.selecting() !== null || this.uploading());
  readonly canSearch = computed(() => !this.searching() && !this.busy());

  ngOnInit(): void {
    this.queryOverride.set(this.data.game.display_name);
    this.runSearch();
  }

  runSearch(): void {
    this.searching.set(true);
    this.errorMessage.set(null);
    const q = this.queryOverride().trim() || undefined;
    this.api.search(this.data.game.id, q).subscribe({
      next: (r) => {
        this.result.set(r);
        this.searching.set(false);
      },
      error: (err: HttpErrorResponse) => {
        this.searching.set(false);
        this.errorMessage.set(err.error?.detail ?? err.message ?? 'Search failed.');
      },
    });
  }

  pick(c: BoxartCandidate): void {
    this.selecting.set(c.source_url);
    this.errorMessage.set(null);
    this.api.select(this.data.game.id, c.source_url, c.name).subscribe({
      next: (game) => {
        this.dialogRef.close({ selected: true, game });
      },
      error: (err: HttpErrorResponse) => {
        this.selecting.set(null);
        this.errorMessage.set(err.error?.detail ?? err.message ?? 'Selection failed.');
      },
    });
  }

  skip(): void {
    this.dialogRef.close({ selected: false });
  }

  scoreClass(score: number): string {
    if (score >= 95) return 'score-perfect';
    if (score >= 85) return 'score-strong';
    return 'score-fair';
  }

  onUploadFileChosen(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    // Reset so picking the same file again still fires change.
    input.value = '';
    if (file) this.uploadFile(file);
  }

  onUploadDragOver(event: DragEvent): void {
    if (event.dataTransfer?.types.includes('Files')) {
      event.preventDefault();
      this.uploadDragOver.set(true);
    }
  }

  onUploadDragLeave(): void {
    this.uploadDragOver.set(false);
  }

  onUploadDrop(event: DragEvent): void {
    event.preventDefault();
    this.uploadDragOver.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) this.uploadFile(file);
  }

  private uploadFile(file: File): void {
    if (this.busy()) return;
    this.uploading.set(true);
    this.errorMessage.set(null);
    this.api.upload(this.data.game.id, file).subscribe({
      next: (game) => {
        this.dialogRef.close({ selected: true, game });
      },
      error: (err: HttpErrorResponse) => {
        this.uploading.set(false);
        this.errorMessage.set(err.error?.detail ?? err.message ?? 'Upload failed.');
      },
    });
  }
}

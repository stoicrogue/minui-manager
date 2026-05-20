import { Component, computed, inject, signal } from '@angular/core';
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
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatChipsModule } from '@angular/material/chips';
import { MatDividerModule } from '@angular/material/divider';

import {
  DetectionConfidence,
  LibraryService,
  SystemDetection,
  UploadResponse,
} from '../../services/library.service';

type Step = 'pick' | 'uploading' | 'review' | 'confirming';

@Component({
  selector: 'app-upload-dialog',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatChipsModule,
    MatDividerModule,
  ],
  templateUrl: './upload-dialog.component.html',
  styleUrl: './upload-dialog.component.scss',
})
export class UploadDialogComponent {
  private readonly api = inject(LibraryService);
  private readonly dialogRef = inject(MatDialogRef<UploadDialogComponent>);
  readonly data = inject<{ initialFile?: File } | null>(MAT_DIALOG_DATA, {
    optional: true,
  });

  readonly step = signal<Step>('pick');
  readonly upload = signal<UploadResponse | null>(null);
  readonly dragOver = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);

  // Form state (initialized from detection after upload)
  readonly systemCode = signal<string>('');
  readonly displayName = signal<string>('');

  readonly canConfirm = computed(
    () =>
      this.step() === 'review' &&
      this.systemCode().length > 0 &&
      this.displayName().trim().length > 0,
  );

  constructor() {
    // If parent passed a dropped file, kick off the upload immediately.
    const f = this.data?.initialFile;
    if (f) {
      this.beginUpload(f);
    }
  }

  onFileChosen(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) {
      this.beginUpload(file);
    }
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(true);
  }

  onDragLeave(): void {
    this.dragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      this.beginUpload(file);
    }
  }

  private beginUpload(file: File): void {
    this.step.set('uploading');
    this.errorMessage.set(null);
    this.api.upload(file).subscribe({
      next: (resp) => {
        this.upload.set(resp);
        this.systemCode.set(resp.detection.detected_code ?? '');
        this.displayName.set(resp.detection.suggested_display_name);
        this.step.set('review');
      },
      error: (err: HttpErrorResponse) => {
        this.step.set('pick');
        this.errorMessage.set(err.error?.detail || err.message || 'Upload failed.');
      },
    });
  }

  confidenceLabel(c: DetectionConfidence): string {
    return { high: 'High', medium: 'Medium', low: 'Low', unknown: 'Unknown' }[c];
  }

  confidenceClass(c: DetectionConfidence): string {
    return `confidence-${c}`;
  }

  confirm(): void {
    const u = this.upload();
    if (!u) return;
    this.step.set('confirming');
    this.errorMessage.set(null);
    this.api
      .confirmDraft(u.draft_id, this.systemCode(), this.displayName().trim())
      .subscribe({
        next: (game) => {
          this.dialogRef.close({ confirmed: true, game });
        },
        error: (err: HttpErrorResponse) => {
          this.step.set('review');
          const detail = err.error?.detail;
          const msg = typeof detail === 'string' ? detail : detail?.message;
          this.errorMessage.set(msg ?? err.message ?? 'Confirm failed.');
        },
      });
  }

  cancel(): void {
    const u = this.upload();
    if (u) {
      // Best-effort cleanup of the draft on the backend; don't block close.
      this.api.cancelDraft(u.draft_id).subscribe({ error: () => {} });
    }
    this.dialogRef.close({ confirmed: false });
  }

  /** Detection's candidate list, or the full registry if unknown. */
  candidates() {
    return this.upload()?.detection.candidates ?? [];
  }

  detection(): SystemDetection | null {
    return this.upload()?.detection ?? null;
  }

  prettySize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }
}

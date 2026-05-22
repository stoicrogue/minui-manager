/** Expand a drag-and-drop into a flat list of Files.
 *
 * When the user drops a folder (not just a single file), the browser's
 * ``dataTransfer.files`` only contains the immediate children — empty
 * for a directory drop on Chromium. Walking ``webkitGetAsEntry`` gives
 * us every file under the directory, which is what we need for
 * multi-disk game folders.
 */
export async function collectFilesFromDrop(
  items: DataTransferItemList,
): Promise<File[]> {
  const files: File[] = [];
  const entries: FileSystemEntry[] = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind !== 'file') continue;
    const entry = (item as any).webkitGetAsEntry?.() as FileSystemEntry | null;
    if (entry) {
      entries.push(entry);
    } else {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  for (const entry of entries) {
    await walkEntry(entry, files);
  }
  return files;
}

async function walkEntry(entry: FileSystemEntry, out: File[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => {
      (entry as FileSystemFileEntry).file(resolve, reject);
    });
    out.push(file);
    return;
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    // readEntries returns at most ~100 at a time — loop until empty.
    while (true) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => {
        reader.readEntries(resolve, reject);
      });
      if (batch.length === 0) break;
      for (const child of batch) {
        await walkEntry(child, out);
      }
    }
  }
}

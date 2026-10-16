// A phone photo is 3-12 MB, and it travels as base64 (a third larger again) in the chat request. The model reads it no
// better at that size than at 1600 px, so photos are shrunk in the browser before they are sent.
const MAX_EDGE = 1600;
const KEEP_AS_IS_BYTES = 800_000;
const JPEG_QUALITY = 0.85;

/** An iPhone photo in its default HEIC format that this browser could not convert (Safari can; Chrome and Edge often cannot). The server does not read
 *  HEIC, so sent as it is the photo would arrive as nothing and the question about it would get a blank answer; better to say so up front. */
export function isUnreadableHeic(file: Pick<File, 'name' | 'type'>): boolean {
  return /^image\/hei[cf]$/i.test(file.type) || /\.hei[cf]$/i.test(file.name);
}

export const HEIC_NOTICE =
  "This browser can't read that photo's format (HEIC). Take a screenshot of the photo and attach that, or set your iPhone camera to Most Compatible (Settings, Camera, Formats) and try again.";

export async function shrinkImageFile(file: File): Promise<File> {
  // GIFs keep their frames; anything that is not an image (PDF, DOCX) is not ours to touch.
  if (!file.type.startsWith("image/") || file.type === "image/gif") return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size <= KEEP_AS_IS_BYTES) {
      bitmap.close?.();
      return file;
    }
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) {
      bitmap.close?.();
      return file;
    }
    context.fillStyle = "#ffffff"; // JPEG has no transparency: a transparent PNG would otherwise turn black
    context.fillRect(0, 0, width, height);
    context.drawImage(bitmap, 0, 0, width, height);
    bitmap.close?.();
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
    if (!blob || blob.size >= file.size) return file; // never make it bigger
    // The server decides the image type from the file extension, so the name follows the new format.
    return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
  } catch {
    return file; // an unreadable or unsupported image goes up as it was
  }
}

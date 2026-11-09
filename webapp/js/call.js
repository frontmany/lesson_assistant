// Звонок урока на LiveKit

import { LESSON_ID, api } from "./lesson.js";

const { DisconnectReason, Room, RoomEvent, Track } = window.LivekitClient;

export { DisconnectReason };

// Вызывать только по нажатию, без жеста браузер не даст микрофон и звук
export async function joinCall(participantId, { onSpeakers, onState, mic = true, cam = false } = {}) {
  const { url, token, participant_id: id } = await api(`/api/lessons/${LESSON_ID}/join`);
  if (participantId && id !== participantId) throw new Error("Нет доступа к уроку");

  const room = new Room({ adaptiveStream: true, dynacast: true });

  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) document.body.append(track.attach());
  });
  room.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((el) => el.remove()));
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => onSpeakers?.(speakers.map((p) => p.identity)));
  room.on(RoomEvent.ConnectionStateChanged, (state) => state !== "disconnected" && onState?.(state));
  room.on(RoomEvent.Disconnected, (reason) => onState?.("disconnected", reason));

  await room.connect(url, token);
  try {
    await room.startAudio();
    if (mic) await room.localParticipant.setMicrophoneEnabled(true);
  } catch (err) {
    await room.disconnect();
    throw err;
  }
  if (cam) {
    try {
      await room.localParticipant.setCameraEnabled(true);
    } catch (err) {
      console.warn("камера не включилась", err);
    }
  }
  return room;
}

export async function toggleMic(room) {
  const enabled = room.localParticipant.isMicrophoneEnabled;
  await room.localParticipant.setMicrophoneEnabled(!enabled);
  return !enabled;
}

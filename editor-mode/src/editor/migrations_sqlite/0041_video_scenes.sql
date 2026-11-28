-- Alembic revision 0053: the scenes of a video idea and the video model's job of each scene.
ALTER TABLE editor.videos ADD COLUMN scenes text;
ALTER TABLE editor.videos ADD COLUMN parts text;

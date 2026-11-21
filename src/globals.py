import os.path

import cv2

from Mon import Config, Logger, get_path_relative_to_exe, og

logger = Logger.get_logger(__name__)


class Globals:

    def __init__(self, exit_event):
        self._yolo_model = None
        self.mini_map_arrow = None
        self.logged_in = False

    def on_show_main_window(self, window):
        """Strip upstream project cards from the About page.

        The framework adds project/link cards pointing at the upstream build.
        They are irrelevant to this portable build and expose its identity, so
        remove the whole section defensively — a framework layout change must
        never prevent the main window from showing.
        """
        try:
            from Mon.ui.qt.about.AboutTab import ProjectCard

            about = getattr(window, 'about_tab', None)
            if about is None:
                return
            for section in list(about.sections):
                if section.findChildren(ProjectCard):
                    about.vBoxLayout.removeWidget(section)
                    about.sections.remove(section)
                    section.deleteLater()
        except Exception as e:
            logger.warning(f'could not strip upstream about cards: {e}')

    @property
    def yolo_model(self):
        if self._yolo_model is None:
            weights = get_path_relative_to_exe(os.path.join("assets", "echo_model", "echo.onnx"))
            if og.config.get("ocr").get("params").get("use_openvino"):
                logger.info("yolo_model Using OpenVinoYolo8Detect")
                from src.OpenVinoYolo8Detect import OpenVinoYolo8Detect
                self._yolo_model = OpenVinoYolo8Detect(
                    weights=weights)
            else:
                logger.info("yolo_model Using OnnxYolo8Detect")
                from src.OnnxYolo8Detect import OnnxYolo8Detect
                self._yolo_model = OnnxYolo8Detect(
                    weights=weights)
        return self._yolo_model

    def yolo_detect(self, image, threshold=0.6, label=-1):
        return self.yolo_model.detect(image, threshold=threshold, label=label)


if __name__ == "__main__":
    glbs = Globals(exit_event=None)

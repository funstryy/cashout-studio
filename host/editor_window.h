// Opening a plugin's own editor window.
//
// A VST3 plugin draws its interface itself; the host only has to give it a
// native window to draw into and then pump messages for it. Reconstructing
// that interface out of the parameter list - sliders labelled "Param 27" -
// is what every DAW avoided for good reason: a wavetable synth or a pitch
// corrector is unusable without its own graph, keyboard and preset browser.
#pragma once

#include "pluginterfaces/vst/ivsteditcontroller.h"

#include <string>

/** Shows the plugin's editor and returns when the user closes the window.
 *  Returns false and fills `error` if the plugin has no editor to show. */
bool runPluginEditor(Steinberg::Vst::IEditController* controller,
                     const std::string& title,
                     std::string& error);

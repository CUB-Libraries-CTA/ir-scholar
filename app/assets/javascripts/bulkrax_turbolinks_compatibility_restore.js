// Restores jQuery's original event-registration method after Bulkrax loads so
// the Turbolinks workaround cannot alter handlers registered by the rest of
// the application.
(function ($, window) {
  var compatibility = window.BulkraxTurbolinksCompatibility;

  if (!compatibility) {
    return;
  }

  if ($.fn.on === compatibility.patchedOn) {
    $.fn.on = compatibility.originalOn;
  }

  delete window.BulkraxTurbolinksCompatibility;
})(jQuery, window);

// Prevents Bulkrax 9.5.1 from binding the same initializer to both ready and
// turbolinks:load, which otherwise makes button handlers run twice on the
// initial page load.
(function ($, window) {
  if (window.BulkraxTurbolinksCompatibility) {
    return;
  }

  var originalOn = $.fn.on;

  function withoutDuplicateReady(types) {
    if (typeof types === 'string') {
      var events = types.match(/\S+/g) || [];

      if (events.indexOf('ready') !== -1 && events.indexOf('turbolinks:load') !== -1) {
        return events.filter(function (eventName) {
          return eventName !== 'ready';
        }).join(' ');
      }
    }

    if (types && typeof types === 'object' &&
      types.ready && types['turbolinks:load'] === types.ready) {
      var normalizedTypes = {};

      Object.keys(types).forEach(function (eventName) {
        if (eventName !== 'ready') {
          normalizedTypes[eventName] = types[eventName];
        }
      });

      return normalizedTypes;
    }

    return types;
  }

  function patchedOn(types) {
    arguments[0] = withoutDuplicateReady(types);
    return originalOn.apply(this, arguments);
  }

  window.BulkraxTurbolinksCompatibility = {
    originalOn: originalOn,
    patchedOn: patchedOn
  };
  $.fn.on = patchedOn;
})(jQuery, window);

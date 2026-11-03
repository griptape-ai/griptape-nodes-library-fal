- Fixed fal nodes polling until the timeout after an HTTP error. A client error such as 400, 401, 403,
  404 or 422 now stops the run at once and shows fal's message. Other errors stop after 5 failed polls in
  a row.

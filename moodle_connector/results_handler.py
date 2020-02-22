import sys
import logging

from state_recorder.file import File
from state_recorder.course import Course
from moodle_connector.request_helper import RequestHelper


class ResultsHandler:
    """
    Fetches and parses the various endpoints in Moodle.
    """

    def __init__(self, request_helper: RequestHelper):
        self.request_helper = request_helper
        # oldest suported moodle version
        self.version = 2011120500

    def setVersion(self, version: int):
        self.version = version

        logging.debug('Detected moodle version: %d' % (version))

    def fetch_userid_and_version(self) -> str:
        """
        Ask the Moodle system for the user id.
        @return: the userid
        """
        result = self.request_helper.post_REST('core_webservice_get_site_info')

        if ("userid" not in result):
            raise RuntimeError(
                'Error could not receive your user ID!')
        userid = result.get("userid", "")

        version = result.get("version", "2011120500")

        try:
            version = int(version.split(".")[0])
        except Exception as e:
            raise RuntimeError(
                'Error could not parse version string: ' +
                '"%s" Error: %s' % (version, e))

        return userid, version

    def fetch_courses(self, userid: str) -> [Course]:
        """
        Queries the Moodle system for all courses the user
        is enrolled in.
        @param userid: the user id
        @return: A list of courses
        """
        data = {
            'userid': userid
        }

        result = self.request_helper.post_REST(
            'core_enrol_get_users_courses', data)

        results = []
        for course in result:
            results.append(
                Course(course.get("id", 0),
                       course.get("fullname", ""), [])
            )
        return results

    def fetch_assignments(self) -> {int: {int: {}}}:
        """
        Fetches the Assignments List for all courses from the
        Moodle system
        @return: A Dictonary of all assignments,
                 idexed by courses, then assignment
        """
        # do this only if version is greater then 2.4
        # because mod_assign_get_assignments will fail
        if (self.version < 2012120300):
            return {}

        # we could add courseids[0],... to the request to download
        # only the assignments of a special course, but we can also
        # just request all assignments at once

        sys.stdout.write('\rDownload assignments information')
        sys.stdout.flush()

        assign_result = self.request_helper.post_REST(
            'mod_assign_get_assignments')

        assign_courses = assign_result.get('courses', [])

        result = {}
        for assign_course in assign_courses:
            course_id = assign_course.get("id", 0)
            course_assigns = {}
            course_assign_objs = assign_course.get('assignments', [])

            for course_assign_obj in course_assign_objs:
                assign_id = course_assign_obj.get("cmid", 0)
                assign_rid = course_assign_obj.get("id", 0)
                assign_files = []
                assign_files += course_assign_obj.get("introfiles", [])
                assign_files += course_assign_obj.get("introattachments", [])

                # normalize
                for assign_file in assign_files:
                    file_type = assign_file.get("type", "")
                    if (file_type is None or file_type == ""):
                        assign_file.update({'type': 'assign_file'})

                course_assigns.update({assign_id: {'id': assign_rid,
                                                   'files': assign_files}
                                       })

            result.update({course_id: course_assigns})

        return result

    def fetch_submissions(self, userid: int,
                          assignments: {int: {int: {}}},
                          download_course_ids: [int],
                          dont_download_course_ids: [int]) -> {int: {int: {}}}:
        """
        Fetches for the assignments list of all courses the additionaly
        submissions. This is kind of waste of resources, beacuse there
        is no api to get all submissions at once
        @param userid: the user id.
        @param assignments: the dictonary of assignments of all courses.
        @param download_course_ids: ids of courses for that sould
                                    be downloaded
        @param dont_download_course_ids: ids of courses for that sould
                                         not be downloaded
        @return: A Dictonary of all assignments,
                 idexed by courses, then assignment
        """
        # do this only if version is greater then 3.1
        # because mod_assign_get_submission_status will fail
        if (self.version < 2016052300):
            return assignments

        intro = '\rDownload submission information'

        counter = 0
        total = 0

        # count total assignments for nice console output
        for course_id in assignments:
            if (not self._should_download_course(
                course_id, download_course_ids,
                    dont_download_course_ids)):
                continue
            for assignment_id in assignments[course_id]:
                total += 1

        for course_id in assignments:
            if (not self._should_download_course(
                course_id, download_course_ids,
                    dont_download_course_ids)):
                continue
            for assignment_id in assignments[course_id]:
                counter += 1
                real_id = assignments[course_id][assignment_id].get('id', 0)
                data = {
                    'userid': userid,
                    'assignid': real_id
                }

                sys.stdout.write(intro + ' %3d/%3d [%6s|%6s]' % (counter,
                                                                 total,
                                                                 course_id,
                                                                 real_id))
                sys.stdout.flush()

                submission = self.request_helper.post_REST(
                    'mod_assign_get_submission_status', data)

                submission_files = self._get_files_of_submission(submission)
                assignments[course_id][assignment_id]['files'] += (
                    submission_files)

        return assignments

    @staticmethod
    def _should_download_course(course_id: int, download_course_ids: [int],
                                dont_download_course_ids: [int]) -> bool:
        """
        Checks if a course is in Whitelist and not in Blacklist
        """
        inBlacklist = (course_id in dont_download_course_ids)
        inWhitelist = (course_id in download_course_ids or
                       len(download_course_ids) == 0)

        return (inWhitelist and not inBlacklist)

    @staticmethod
    def _get_files_of_submission(submission: {}) -> []:
        result = []
        # get own submissions
        lastattempt = submission.get('lastattempt', {})
        l_submission = lastattempt.get('submission', {})
        l_teamsubmission = lastattempt.get('teamsubmission', {})

        # get teachers feedback
        feedback = submission.get('feedback', {})

        result += ResultsHandler._get_files_of_pllugins(l_submission)
        result += ResultsHandler._get_files_of_pllugins(l_teamsubmission)
        result += ResultsHandler._get_files_of_pllugins(feedback)

        return result

    @staticmethod
    def _get_files_of_pllugins(obj: {}) -> []:
        result = []
        plugins = obj.get('plugins', [])

        for plugin in plugins:
            fileareas = plugin.get('fileareas', [])

            for filearea in fileareas:
                files = filearea.get('files', [])

                for file in files:
                    file_type = file.get("type", "")
                    if (file_type is None or file_type == ""):
                        file.update({'type': 'submission_file'})

                    result.append(file)

        return result

    @staticmethod
    def _get_files_in_sections(course_sections: [],
                               assignments: {int: {}}) -> [File]:
        """
        Iterates over all sections of a course to find files (or modules).
        @param course_sections: The course object returned by Moodle,
                                containing the sections of the course.
        @param assignments: the dictonary of assignments of the course.
        @return: A list of files of the course.
        """
        files = []
        for section in course_sections:
            section_name = section.get("name", "")
            section_modules = section.get("modules", [])
            files += ResultsHandler._get_files_in_modules(section_name,
                                                          section_modules,
                                                          assignments)
        return files

    @staticmethod
    def _get_files_in_modules(section_name: str,
                              section_modules: [],
                              assignments: {int: {}}) -> [File]:
        """
        Iterates over all modules to find files (or content) in them.
        @param section_name: The name of the section to be iterated over.
        @param section_modules: The modules of the section.
        @param assignments: the dictionary of assignments of the course.
        @return: A list of files of the section.
        """
        files = []
        for module in section_modules:
            module_name = module.get("name", "")
            module_modname = module.get("modname", "")
            module_id = module.get("id", 0)

            module_contents = module.get("contents", [])

            if (module_modname in ["resource", "folder", "url"]):
                files += ResultsHandler._handle_files(section_name,
                                                      module_name,
                                                      module_modname,
                                                      module_id,
                                                      module_contents)

            elif (module_modname == "assign"):

                # find assign with same module_id
                assign = assignments.get(module_id, {})
                assign_files = assign.get('files', [])

                files += ResultsHandler._handle_files(section_name,
                                                      module_name,
                                                      module_modname,
                                                      module_id,
                                                      assign_files)

        return files

    @staticmethod
    def _handle_files(section_name: str, module_name: str,
                      module_modname: str, module_id: str,
                      module_contents: []) -> [File]:
        """
        Iterates over all files that are in a module or assignment and
        returns a list of all files
        @param module_contents: The list of content of the module
                                or assignment.
        @params: All necessary parameters to create a file.
        @return: A list of files that exist in a module.
        """
        files = []
        for content in module_contents:
            content_type = content.get("type", "")
            content_filename = content.get("filename", "")
            content_filepath = content.get("filepath", "/")
            if content_filepath is None:
                content_filepath = '/'
            content_filesize = content.get("filesize", 0)
            content_fileurl = content.get("fileurl", "")
            content_timemodified = content.get("timemodified", 0)
            content_isexternalfile = content.get("isexternalfile", False)

            files.append(File(
                module_id=module_id,
                section_name=section_name,
                module_name=module_name,
                content_filepath=content_filepath,
                content_filename=content_filename,
                content_fileurl=content_fileurl,
                content_filesize=content_filesize,
                content_timemodified=content_timemodified,
                module_modname=module_modname,
                content_type=content_type,
                content_isexternalfile=content_isexternalfile)
            )
        return files

    def fetch_files(self, course_id: str, assignments: {int: {}}) -> [File]:
        """
        Queries the Moodle system for all the files that
        are present in a course
        @param course_id: The id of the course for which you want to enquire.
        @return: A list of Files
        """

        data = {
            'courseid': course_id
        }
        course_sections = self.request_helper.post_REST(
            'core_course_get_contents', data)

        files = self._get_files_in_sections(course_sections, assignments)

        return files
